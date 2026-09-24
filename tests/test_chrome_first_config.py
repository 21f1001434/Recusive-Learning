from pathlib import Path

import yaml

from hip_id_agent.config import load_config, AppConfig
from hip_id_agent.browser_session import BrowserSession


def test_config_example_uses_google_chrome_by_default():
    raw = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    assert raw["portal"]["chromium_channel"] == "chrome"
    assert raw["portal"]["browser_user_data_dir"].endswith("chrome_profile")
    assert "chrome_executable_path" in raw["portal"]
    assert "edge_executable_path" in raw["portal"]
    assert raw["portal"]["fallback_to_edge"] is True


def test_bundled_config_yaml_exists_and_uses_chrome():
    assert Path("config.yaml").exists()
    cfg = load_config("config.yaml")
    assert cfg.portal.chromium_channel == "chrome"
    assert cfg.portal.chrome_executable_path is None
    assert cfg.portal.browser_user_data_dir.endswith("chrome_profile")


def test_portal_config_default_is_chrome():
    cfg = AppConfig()
    assert cfg.portal.chromium_channel == "chrome"
    assert cfg.portal.browser_user_data_dir.endswith("chrome_profile")
    assert cfg.portal.fallback_to_edge is True


def test_browser_launch_kwargs_support_explicit_edge_override(tmp_path):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "edge-profile")
    cfg.portal.edge_executable_path = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
    cfg.portal.chromium_channel = "msedge"
    session = BrowserSession(cfg, tmp_path / "run")

    # Chrome is the shipped default, but an operator may still explicitly select
    # Edge for a locked-down workstation without changing executor semantics.
    assert session.config.portal.edge_executable_path.endswith("msedge.exe")
    assert session.config.portal.chromium_channel == "msedge"


def test_browser_candidates_default_to_chrome_then_edge_then_playwright(tmp_path):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.edge_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    session = BrowserSession(cfg, tmp_path / "run")
    candidates = session._managed_browser_candidates()
    assert [x["name"] for x in candidates] == ["chrome", "edge", "playwright_chromium"]
    assert candidates[0]["channel"] == "chrome"
    assert candidates[1]["channel"] == "msedge"
    assert len({x["profile"] for x in candidates}) == 3


def test_shipped_configs_are_portable_aligned_and_chrome_first():
    for name in ("config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"):
        raw = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
        assert raw["reporting"]["runs_dir"] == "./runs"
        assert raw["mcp"]["playwright_mcp_command"] == "auto"
        assert raw["mcp"]["chrome_devtools_command"] == "auto"
        assert "portal_learning" in raw
        assert "runtime_self_heal" in raw
        assert raw["portal"]["chromium_channel"] == "chrome"
        assert raw["portal"]["browser_user_data_dir"].endswith("chrome_profile")
        assert raw["portal"]["fallback_to_edge"] is True
        assert raw["portal"]["edge_user_data_dir"].endswith("edge_profile")
    strict = yaml.safe_load(Path("config.mcp-required.windows.yaml").read_text(encoding="utf-8"))
    assert strict["mcp"]["use_playwright_mcp"] is True
    assert strict["mcp"]["use_chrome_devtools_mcp"] is True
    assert strict["mcp"]["use_hip_intelligence_mcp"] is True
    assert strict["mcp"]["hip_intelligence_mcp_required"] is True
