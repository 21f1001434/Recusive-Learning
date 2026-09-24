from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import hip_id_agent
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, PyAutoGUIConfig
from hip_id_agent.live_readiness import build_live_readiness_report
from hip_id_agent.mcp_stdio import MCPTool
from hip_id_agent.pyautogui_mcp import PyAutoGUIMCPBackend
from hip_id_agent.pyautogui_tool import PyAutoGUIFallbackTool, PyAutoGUIUnavailable


REQUIRED = {
    "pyautogui_size", "pyautogui_position", "pyautogui_click",
    "pyautogui_write", "pyautogui_press", "pyautogui_hotkey", "pyautogui_screenshot",
}


class FakeClient:
    def __init__(self, tools=None, responses=None):
        names = tools or REQUIRED
        self.tools = {
            name: MCPTool(name, input_schema={"type": "object", "properties": self._props(name)})
            for name in names
        }
        self.responses = responses or {}
        self.calls = []
        self.started = False

    @staticmethod
    def _props(name):
        if name.endswith("click"):
            return {"x": {}, "y": {}, "clicks": {}, "button": {}, "interval": {}, "duration": {}}
        if name.endswith("write"):
            return {"message": {}, "interval": {}}
        if name.endswith("press"):
            return {"keys": {}, "presses": {}, "interval": {}}
        if name.endswith("hotkey"):
            return {"keys": {}, "interval": {}}
        if name.endswith("screenshot"):
            return {"region": {}}
        return {}

    async def start(self):
        self.started = True

    async def close(self):
        self.started = False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name in self.responses:
            return self.responses[name]
        if name == "pyautogui_size":
            return {"content": [{"type": "text", "text": json.dumps({"width": 1920, "height": 1080})}]}
        if name == "pyautogui_position":
            return {"content": [{"type": "text", "text": json.dumps({"x": 100, "y": 200})}]}
        return {"content": [{"type": "text", "text": "null"}]}


class FakeMCP:
    started = True
    def __init__(self):
        self.calls = []
    def capability_status(self):
        return {"available": True, "required_tool_status": {x: True for x in REQUIRED}}
    async def size(self):
        return 1920, 1080
    async def click(self, x, y, **kwargs):
        self.calls.append(("click", x, y, kwargs))
    async def hotkey(self, keys):
        self.calls.append(("hotkey", list(keys)))
    async def write(self, text, **kwargs):
        self.calls.append(("write", text, kwargs))
    async def press(self, key):
        self.calls.append(("press", key))


class FakeLocator:
    @property
    def first(self):
        return self
    async def scroll_into_view_if_needed(self, timeout=0):
        return None
    async def bounding_box(self, timeout=0):
        return {"x": 100.0, "y": 200.0, "width": 100.0, "height": 40.0}


class FakePage:
    async def bring_to_front(self):
        return None
    async def evaluate(self, script):
        return {
            "screenX": 0, "screenY": 0, "outerWidth": 1920, "outerHeight": 1080,
            "innerWidth": 1920, "innerHeight": 1080, "screenWidth": 1920, "screenHeight": 1080,
            "vvX": 0, "vvY": 0,
        }


def test_01_version_remains_promoted_after_v218():
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")


def test_02_pyautogui_mcp_dependency_is_windows_pinned():
    req = Path("requirements.txt").read_text(encoding="utf-8").lower()
    project = Path("pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pyautogui-mcp==2026.1.101837" in req
    assert "pyautogui-mcp==2026.1.101837" in project
    assert "platform_system" in req and "platform_system" in project


def test_03_default_policy_prefers_mcp_but_keeps_it_optional():
    cfg = PyAutoGUIConfig()
    assert cfg.enabled is True
    assert cfg.mcp_enabled is True
    assert cfg.mcp_required is False
    assert cfg.prefer_mcp is True
    assert cfg.interaction_mode == "primary"
    assert cfg.allow_mutation_clicks is True
    assert cfg.native_target_confidence_threshold >= 0.97


def test_04_backend_defaults_to_current_python_module_stdio(tmp_path: Path):
    cfg = AppConfig()
    backend = PyAutoGUIMCPBackend.from_config(cfg, tmp_path)
    assert backend.client.command == sys.executable
    assert backend.client.args[:4] == ["-m", "pyautogui_mcp", "--transport", "stdio"]
    assert "--prefix" in backend.client.args
    assert "pyautogui_" in backend.client.args


def test_05_capability_contract_requires_desktop_tools(tmp_path: Path):
    client = FakeClient()
    backend = PyAutoGUIMCPBackend(client, tmp_path)
    backend.started = True
    status = backend.capability_status()
    assert status["available"] is True
    assert REQUIRED <= set(status["required_tool_status"])
    assert all(status["required_tool_status"].values())


def test_06_missing_tool_makes_backend_capability_unavailable(tmp_path: Path):
    client = FakeClient(tools=REQUIRED - {"pyautogui_click"})
    backend = PyAutoGUIMCPBackend(client, tmp_path)
    backend.started = True
    status = backend.capability_status()
    assert status["available"] is False
    assert status["required_tool_status"]["pyautogui_click"] is False


def test_07_size_and_position_parse_mcp_structured_text(tmp_path: Path):
    backend = PyAutoGUIMCPBackend(FakeClient(), tmp_path)
    assert asyncio.run(backend.size()) == (1920, 1080)
    assert asyncio.run(backend.position()) == (100, 200)


def test_08_click_uses_coordinate_tool_schema(tmp_path: Path):
    client = FakeClient()
    backend = PyAutoGUIMCPBackend(client, tmp_path)
    asyncio.run(backend.click(500, 300, duration=0.2))
    name, args = client.calls[-1]
    assert name == "pyautogui_click"
    assert args["x"] == 500 and args["y"] == 300
    assert args["button"] == "left"
    assert args["duration"] == 0.2


def test_09_write_hotkey_press_are_explicit_tools(tmp_path: Path):
    client = FakeClient()
    backend = PyAutoGUIMCPBackend(client, tmp_path)
    asyncio.run(backend.hotkey(["ctrl", "a"]))
    asyncio.run(backend.write("ABC", interval=0.01))
    asyncio.run(backend.press("enter"))
    assert [x[0] for x in client.calls[-3:]] == ["pyautogui_hotkey", "pyautogui_write", "pyautogui_press"]


def test_10_fallback_tool_prefers_mcp_for_proven_locator(tmp_path: Path):
    cfg = AppConfig()
    tool = PyAutoGUIFallbackTool(cfg, tmp_path)
    mcp = FakeMCP()
    tool.set_mcp_backend(mcp)
    out = asyncio.run(tool.click_locator(FakePage(), FakeLocator(), action="+ Add", selector="#add"))
    assert out["pass"] is True
    assert out["executor"] == "pyautogui-mcp"
    assert mcp.calls and mcp.calls[0][0] == "click"


def test_11_mcp_fill_uses_click_ctrl_a_write(tmp_path: Path):
    cfg = AppConfig()
    tool = PyAutoGUIFallbackTool(cfg, tmp_path)
    mcp = FakeMCP(); tool.set_mcp_backend(mcp)
    out = asyncio.run(tool.fill_locator(FakePage(), FakeLocator(), "ABC", selector="#field"))
    assert out["executor"] == "pyautogui-mcp"
    assert [x[0] for x in mcp.calls] == ["click", "hotkey", "write"]


def test_12_governed_exact_locator_mutation_can_use_pyautogui_primary(tmp_path: Path):
    cfg = AppConfig()
    tool = PyAutoGUIFallbackTool(cfg, tmp_path)
    mcp = FakeMCP()
    tool.set_mcp_backend(mcp)
    out = asyncio.run(tool.click_locator(FakePage(), FakeLocator(), action="Deploy", selector="#deploy"))
    assert out["pass"] is True
    assert out["executor"] == "pyautogui-mcp"
    assert mcp.calls and mcp.calls[0][0] == "click"


def test_13_native_coordinate_recovery_requires_high_confidence_provenance(tmp_path: Path):
    cfg = AppConfig(); tool = PyAutoGUIFallbackTool(cfg, tmp_path); tool.set_mcp_backend(FakeMCP())
    with pytest.raises(PyAutoGUIUnavailable):
        asyncio.run(tool.click_screen_point(x=400, y=400, action="Open file dialog", evidence={"source": "vision", "confidence": 0.80}))


def test_14_native_coordinate_recovery_accepts_whitelisted_safe_high_confidence(tmp_path: Path):
    cfg = AppConfig(); tool = PyAutoGUIFallbackTool(cfg, tmp_path); mcp = FakeMCP(); tool.set_mcp_backend(mcp)
    out = asyncio.run(tool.click_screen_point(
        x=400, y=400, action="Open file dialog",
        evidence={"source": "native_dialog", "confidence": 0.99, "reason": "OS dialog button uniquely detected"},
    ))
    assert out["pass"] is True
    assert out["executor"] == "pyautogui-mcp"


def test_15_native_coordinate_recovery_blocks_mutation_even_with_high_confidence(tmp_path: Path):
    cfg = AppConfig(); tool = PyAutoGUIFallbackTool(cfg, tmp_path); tool.set_mcp_backend(FakeMCP())
    with pytest.raises(PyAutoGUIUnavailable):
        asyncio.run(tool.click_screen_point(x=400, y=400, action="Save", evidence={"source": "vision", "confidence": 0.99}))


def _probe(py_available: bool) -> dict:
    pw = ["browser_navigate", "browser_snapshot", "browser_find", "browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_take_screenshot"]
    hip = [
        "build_web_representation", "plan_form_action", "resolve_semantic_control", "rank_semantic_candidates",
        "verify_semantic_action_effect", "get_semantic_control_fingerprint", "get_semantic_control_capabilities",
        "hip_get_current_surface", "hip_get_form_schema", "hip_find_control", "hip_find_owned_popup",
        "hip_get_repeatable_rows", "hip_get_required_fields", "hip_get_current_values", "hip_compare_expected_actual",
        "hip_get_safe_actions", "hip_verify_action_effect", "hip_get_route_identity", "hip_get_form_generation",
    ]
    return {
        "playwright_mcp": {"available": True, "required_tool_status": {x: True for x in pw}},
        "chrome_devtools_mcp": {"available": True, "required_tool_status": {"take_snapshot": True, "list_network_requests": True, "list_console_messages": True}},
        "hip_intelligence_mcp": {"available": True, "required_tool_status": {x: True for x in hip}},
        "pyautogui_mcp": {"available": py_available, "required_tool_status": {x: py_available for x in REQUIRED}},
    }


def _readiness(py_required: bool, py_available: bool) -> dict:
    cfg = AppConfig()
    cfg.pyautogui.mcp_required = py_required
    return build_live_readiness_report(
        fingerprint="v218",
        static_preflight={"pass": True, "skill_vetting": {"pass": True}, "autogen": {"pass": True}},
        browser_probe={"pass": True}, mcp_probe=_probe(py_available), text_probe={"pass": True},
        vision_probe={"pass": True}, path_probe={"pass": True}, process_state={"running": False}, config=cfg,
    )


def test_16_optional_pyautogui_mcp_unavailable_is_warning_not_blocker():
    report = _readiness(False, False)
    assert report["pass"] is True
    assert any(x["id"] == "pyautogui_mcp_live" for x in report["warnings"])


def test_17_required_pyautogui_mcp_unavailable_blocks_live_go():
    report = _readiness(True, False)
    assert report["pass"] is False
    assert any(x["id"] == "pyautogui_mcp_live" for x in report["blockers"])


def test_18_required_pyautogui_mcp_available_passes_live_go():
    report = _readiness(True, True)
    assert report["pass"] is True


def test_19_browser_session_runtime_attaches_and_closes_pyautogui_mcp():
    src = inspect.getsource(BrowserSession._start_required_mcp_backends)
    close = inspect.getsource(BrowserSession.close)
    assert "PyAutoGUIMCPBackend.from_config" in src
    assert "self.pyautogui_tool.set_mcp_backend" in src
    assert '"pyautogui-mcp"' in src
    assert "self.pyautogui_mcp_backend.close" in close


def test_20_browser_action_provenance_uses_actual_desktop_executor():
    click = inspect.getsource(BrowserSession.click_and_wait)
    fill = inspect.getsource(BrowserSession.fill_and_log)
    press = inspect.getsource(BrowserSession.press_and_log)
    for src in (click, fill, press):
        assert "pyautogui_executor" in src
        assert "pyautogui-mcp-primary" in src


def test_21_browser_backend_probe_publishes_pyautogui_mcp_channel():
    import hip_id_agent.browser_backend as m
    src = inspect.getsource(m.validate_dual_browser_mcps)
    assert "validate_pyautogui_mcp" in src
    assert '"pyautogui_mcp"' in src
    assert '"desktop_primary"' in src


def test_22_shipped_yaml_configs_enable_pyautogui_primary_policy():
    for name in ["config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"]:
        doc = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
        py = doc["pyautogui"]
        assert py["mcp_enabled"] is True
        assert py["prefer_mcp"] is True
        assert py["interaction_mode"] == "primary"
        assert py["primary_for_clicks"] is True
        assert py["primary_for_form_fill"] is True
        assert py["allow_mutation_clicks"] is True
        assert float(py["native_target_confidence_threshold"]) >= 0.97


def test_23_pyautogui_mcp_is_promoted_to_primary_physical_executor():
    readiness = inspect.getsource(build_live_readiness_report)
    assert "PyAutoGUI MCP primary visible-desktop executor" in readiness
    assert "PyAutoGUI MCP primary physical click/type/key" in readiness
    assert "Playwright MCP deterministic fallback" in readiness


def test_24_direct_pyautogui_remains_compatibility_fallback_only(tmp_path: Path):
    cfg = AppConfig()
    cfg.pyautogui.allow_local_fallback_if_mcp_unavailable = False
    tool = PyAutoGUIFallbackTool(cfg, tmp_path)
    with pytest.raises(PyAutoGUIUnavailable):
        tool._load()


def test_25_mcp_server_never_receives_arbitrary_python_execution():
    src = Path("hip_id_agent/pyautogui_mcp.py").read_text(encoding="utf-8")
    assert "run_python_with_pyautogui" not in src
    assert "exec(" not in src
    assert "eval(" not in src
    for name in ["click", "write", "press", "hotkey", "screenshot", "size", "position"]:
        assert f'_name("{name}")' in src
