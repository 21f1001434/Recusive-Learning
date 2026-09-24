from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.browser_use_bridge import BrowserUseStateBridge
from hip_id_agent.config import load_config
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController
from hip_id_agent.semantic_control import INVENTORY_JS, SemanticActionGate


def _sem_cfg(**overrides):
    base = dict(
        enabled=True,
        execute_confidence_threshold=0.90,
        anchored_execute_confidence_threshold=0.64,
        structural_opener_confidence_threshold=0.52,
        anchored_ambiguity_margin=0.02,
        prefer_vetted_locator_anchor=True,
        reobserve_confidence_threshold=0.75,
        self_heal_confidence_threshold=0.55,
        ambiguity_margin=0.08,
        require_playwright_mcp_evidence=True,
        require_devtools_evidence=True,
        use_hip_intelligence_mcp_consensus=True,
        require_hip_intelligence_mcp_evidence=True,
        strict_external_evidence=False,
        use_vision_for_ambiguity=True,
        vision_confirmation_boost=0.08,
        fail_closed=True,
        require_post_action_effect=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_custom_navigation_target_is_always_in_semantic_inventory_query():
    # Real failure: the deterministic navigation locator could be marked with the
    # HIP anchor but omitted from the inventory when it was a custom DIV/DDS node.
    assert "[data-hip-semantic-anchor],input" in INVENTORY_JS


def test_safe_structural_navigation_uses_anchored_threshold(tmp_path: Path):
    gate = SemanticActionGate(_sem_cfg(), run_dir=tmp_path / "run", memory_root=tmp_path / "memory")
    policy = gate._execution_policy(action="click", label="Open Biz Flow navigation", anchored=True)
    assert policy["structural"] is True
    assert policy["threshold"] == 0.52
    assert policy["margin"] == 0.0


def test_final_mutations_never_enter_lower_structural_lane(tmp_path: Path):
    gate = SemanticActionGate(_sem_cfg(), run_dir=tmp_path / "run", memory_root=tmp_path / "memory")
    for label in ["Save", "Deploy", "Submit", "Migrate", "Delete"]:
        policy = gate._execution_policy(action="click", label=label, anchored=True)
        assert policy["structural"] is False
        assert policy["threshold"] == 0.64
        assert policy["margin"] == 0.02


def test_exact_real_browser_disconnect_messages_are_classified_for_restart():
    messages = [
        "Failed to open new tab - no browser is open",
        "Cannot navigate - browser not connected",
        "All 3 reconnection attempts failed",
        "ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection",
    ]
    for message in messages:
        assert RuntimeSelfHealController.classify_failure(message) == "browser_disconnected"


class _ReconnectTask:
    def __init__(self): self.cancelled = False
    def done(self): return False
    def cancel(self): self.cancelled = True


class _RootCDP:
    def __init__(self): self.stopped = False
    async def stop(self): self.stopped = True


class _BrowserThatMustNotBeStopped:
    def __init__(self):
        self._reconnect_task = _ReconnectTask()
        self._cdp_client_root = _RootCDP()
        self.public_stop_called = False
        self.public_kill_called = False
    async def stop(self):
        self.public_stop_called = True
        raise AssertionError("Browser.stop() must never be called by HIP observer detach")
    async def kill(self):
        self.public_kill_called = True
        raise AssertionError("Browser.kill() must never be called by HIP observer detach")


def test_browser_use_detach_cannot_reset_or_kill_hip_browser():
    bridge = BrowserUseStateBridge(enabled=True, cdp_url="http://127.0.0.1:9237")
    browser = _BrowserThatMustNotBeStopped()
    bridge.browser = browser
    bridge.status.attached = True
    asyncio.run(bridge.stop())
    assert browser.public_stop_called is False
    assert browser.public_kill_called is False
    assert browser._reconnect_task.cancelled is True
    assert browser._cdp_client_root.stopped is True
    assert bridge.browser is None
    assert bridge.status.attached is False


def test_all_shipped_configs_protect_managed_browser_lifecycle():
    for name in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        cfg = load_config(name)
        assert cfg.browser_use.safe_managed_browser_mode is True, name
        assert cfg.browser_use.non_invasive_detach is True, name
        assert cfg.semantic_understanding.prefer_vetted_locator_anchor is True, name
        assert cfg.semantic_understanding.structural_opener_confidence_threshold == 0.52, name
