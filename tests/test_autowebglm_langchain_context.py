from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge
from hip_id_agent.langchain_browser_toolkit import LangChainBrowserToolkitBridge
from hip_id_agent.config import load_config
from hip_id_agent.expert_skills import build_context_budget


class FakePage:
    def __init__(self, url="https://hip.example/data-maps"):
        self.url = url
        self._context = SimpleNamespace(pages=[self])

    def context(self):
        return self._context

    async def title(self):
        return "HIP Data Map"

    async def evaluate(self, _script):
        return {
            "html": '<html><e id="1" role="combobox" label="Version"></e><e id="2" role="button" label="+ Add"></e></html>',
            "position": {"x": 0, "y": 120, "maxX": 0, "maxY": 1800, "viewportW": 1440, "viewportH": 950},
            "title": "HIP Data Map",
            "url": self.url,
            "elementCount": 2,
        }


def _awg_config(**overrides):
    values = dict(
        enabled=True,
        recovery_only=True,
        allowed_actions=["click", "hover", "select", "type_string", "scroll_page", "go", "jump_to", "switch_tab", "finish"],
        native_model_command=[],
        use_existing_dell_aia=False,
        max_html_chars=70000,
        max_history_actions=40,
        max_tabs=12,
        max_prompt_chars=110000,
        timeout_seconds=20,
        require_agentq_reward_gate=True,
        block_mutation_labels=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_autowebglm_observation_contains_official_style_inputs_without_values():
    bridge = AutoWebGLMRecoveryBridge(_awg_config())
    import asyncio
    obs = asyncio.run(bridge.build_observation(page=FakePage(), task="Fill Data Map", history=[{"action": "select", "field": "Version", "success": False}]))
    assert obs["available"] is True
    assert obs["task"] == "Fill Data Map"
    assert "simplified_html" in obs
    assert obs["current_position"]["y"] == 120
    assert obs["previous_operations"]
    assert "select(id, option)" in obs["action_space"]
    assert "value=" not in obs["simplified_html"].lower()


def test_autowebglm_action_parser_and_mutation_guard():
    bridge = AutoWebGLMRecoveryBridge(_awg_config())
    parsed = bridge.parse_action('select("Version", "1.0")')
    assert parsed["valid"] is True
    assert parsed["action"] == "select"
    assert parsed["args"] == ["Version", "1.0"]
    safe, _ = bridge._safe_action(parsed)
    assert safe is True
    unsafe = bridge.parse_action('click("Save")')
    safe, reason = bridge._safe_action(unsafe)
    assert safe is False
    assert "mutation" in reason


def test_autowebglm_protocol_can_operate_observation_only_without_second_browser():
    import asyncio
    bridge = AutoWebGLMRecoveryBridge(_awg_config(use_existing_dell_aia=False))
    result = asyncio.run(bridge.propose(page=FakePage(), task="Recover Document Type", history=[]))
    assert result["status"] == "observation_only"
    assert result["source"] == "autowebglm_protocol"
    assert result["observation"]["available"] is True


class FakeTool:
    def __init__(self, name):
        self.name = name

    async def ainvoke(self, payload):
        if self.name == "current_webpage":
            return "https://hip.example/document-types"
        if self.name == "extract_text":
            return "Document Type Add form"
        if self.name == "get_elements":
            return "combobox Name; combobox Usage; button Add"
        if self.name == "extract_hyperlinks":
            return "[]"
        return "UNSAFE"


class FakeToolkit:
    @classmethod
    def from_browser(cls, async_browser=None, **_kwargs):
        assert async_browser == "same-cdp-browser"
        return cls()

    def get_tools(self):
        # Include unsafe tools on purpose; bridge must filter them out.
        return [FakeTool(x) for x in ["current_webpage", "extract_text", "get_elements", "extract_hyperlinks", "click_element", "navigate_browser"]]


class FakeChromium:
    async def connect_over_cdp(self, url):
        assert url == "http://127.0.0.1:9237"
        return "same-cdp-browser"


def test_langchain_toolkit_uses_same_cdp_and_filters_mutating_tools(monkeypatch):
    import asyncio

    lc = types.ModuleType("langchain_community")
    agent_toolkits = types.ModuleType("langchain_community.agent_toolkits")
    playwright_mod = types.ModuleType("langchain_community.agent_toolkits.playwright")
    playwright_mod.PlayWrightBrowserToolkit = FakeToolkit
    monkeypatch.setitem(sys.modules, "langchain_community", lc)
    monkeypatch.setitem(sys.modules, "langchain_community.agent_toolkits", agent_toolkits)
    monkeypatch.setitem(sys.modules, "langchain_community.agent_toolkits.playwright", playwright_mod)

    cfg = SimpleNamespace(
        enabled=True, read_only=True, fail_open_if_unavailable=False, timeout_seconds=2,
        max_text_chars=30000, max_element_chars=30000,
        allowed_tools=["current_webpage", "extract_text", "get_elements", "extract_hyperlinks"],
    )
    bridge = LangChainBrowserToolkitBridge(cfg)
    status = asyncio.run(bridge.start(playwright=SimpleNamespace(chromium=FakeChromium()), cdp_url="http://127.0.0.1:9237"))
    assert status["attached"] is True
    assert "click_element" not in status["tools"]
    assert "navigate_browser" not in status["tools"]
    ctx = asyncio.run(bridge.recovery_context())
    assert ctx["available"] is True
    assert "Document Type Add form" in ctx["extract_text"]["result"]
    assert ctx["policy"].startswith("LangChain toolkit is read-only")


def test_default_context_budget_is_materially_larger_and_phase_local():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    assert cfg.expert_skills.context_max_chars >= 64000
    assert cfg.expert_skills.recovery_context_max_chars >= 128000
    assert cfg.runtime_self_heal.aia_advisor_context_max_chars >= 64000
    assert cfg.autowebglm.enabled is True
    assert cfg.langchain_browser_toolkit.enabled is True
    payload = {"objects": {"data_map": {"name": "x"}, "rule": {"name": "r"}}}
    budget = build_context_budget(phases=["data_map"], input_payload=payload, max_serialized_chars=cfg.expert_skills.context_max_chars)
    assert budget["selected_input_keys"] == ["data_map"]
    assert "rule" not in budget["selected_input_keys"]
