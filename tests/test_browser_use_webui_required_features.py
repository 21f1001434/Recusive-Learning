from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config
from hip_id_agent import process_control


ROOT = Path(__file__).resolve().parents[1]


def test_browser_use_webui_required_config_surface_is_present():
    cfg = AppConfig().browser_use
    assert cfg.enabled is True
    assert cfg.attach_same_browser is True
    assert cfg.use_own_browser is False
    assert cfg.keep_browser_open is True
    assert cfg.save_downloads is True
    assert cfg.save_session_history is True
    assert cfg.record_video is False
    assert cfg.capture_playwright_trace is False
    assert cfg.capture_state_on_phase_transition is True


def test_shipped_yaml_exposes_browser_use_session_evidence_controls():
    cfg = load_config(ROOT / "config.yaml").browser_use
    assert cfg.dynamic_repeatable_rows is True
    assert cfg.keep_browser_open is True
    assert cfg.record_video_width == 1440
    assert cfg.record_video_height == 950
    assert cfg.trace_screenshots is True
    assert cfg.trace_snapshots is True
    assert cfg.max_history_entries >= 100


def test_bun_tooling_includes_platform_ui_and_pinned_mcp_packages():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "1.9.2"
    assert package["scripts"]["mcp:playwright"].startswith("npx ")
    assert package["scripts"]["mcp:chrome"].startswith("npx ")
    assert package["scripts"]["mcp:playwright:bun"].startswith("bunx --bun")
    assert package["scripts"]["mcp:chrome:bun"].startswith("bunx --bun")
    assert "webui/platform.js" in package["scripts"]["platform"]
    assert "streamlit" not in package["scripts"]["ui"].lower()
    assert package["devDependencies"]["@playwright/mcp"] == "0.0.79"
    assert package["devDependencies"]["chrome-devtools-mcp"] == "1.6.0"


def test_own_browser_cdp_is_shared_with_governed_executor_and_mcps():
    start_source = inspect.getsource(BrowserSession.start)
    mcp_source = inspect.getsource(BrowserSession._start_required_mcp_backends)
    bridge_source = inspect.getsource(BrowserSession._start_browser_use_bridge)
    assert "connect_over_cdp" in start_source
    assert "use_own_browser" in start_source
    assert "self._cdp_endpoint" in start_source
    assert "self._cdp_endpoint" in mcp_source
    assert "self._cdp_endpoint" in bridge_source


def test_optional_recording_trace_download_and_history_are_real_execution_paths():
    source = inspect.getsource(BrowserSession)
    assert '"record_video_dir"' in source
    assert "context.tracing.start" in source
    assert "context.tracing.stop" in source
    assert 'page.on("download"' in source
    assert "download.save_as" in source
    assert "session_history.json" in source


class _FakeProc:
    def __init__(self):
        self.suspended = False
        self.resumed = False
    def is_running(self):
        return True
    def status(self):
        return "running"
    def suspend(self):
        self.suspended = True
    def resume(self):
        self.resumed = True


class _FakePsutil:
    def __init__(self, proc):
        self.proc = proc
    def Process(self, pid):
        assert pid == 1234
        return self.proc
    def pid_exists(self, pid):
        return pid == 1234


def test_pause_resume_suspends_agent_controller_without_tree_kill(monkeypatch):
    proc = _FakeProc()
    fake = _FakePsutil(proc)
    monkeypatch.setattr(process_control, "_psutil", lambda: fake)
    paused = process_control.pause_process(1234)
    resumed = process_control.resume_process(1234)
    assert paused["status"] == "paused"
    assert resumed["status"] == "resumed"
    assert proc.suspended is True and proc.resumed is True
    # The helper intentionally controls only the Python controller PID so Chrome
    # remains interactive for human Dell SSO/recovery.
    assert "children" not in inspect.getsource(process_control.pause_process)


@pytest.mark.asyncio
async def test_download_persistence_sanitizes_filename_and_writes_manifest(tmp_path):
    cfg = AppConfig()
    cfg.browser_use.download_dir = "downloads"
    session = BrowserSession(cfg, tmp_path)

    class Download:
        suggested_filename = "../unsafe?.txt"
        url = "https://developer.dell.com/export?token=secret"
        async def save_as(self, path):
            Path(path).write_text("ok", encoding="utf-8")

    await session._save_download(Download())
    assert len(session.download_records) == 1
    row = session.download_records[0]
    assert row["status"] == "saved"
    assert ".." not in Path(row["saved_path"]).name
    assert "?" not in Path(row["saved_path"]).name
    manifest = tmp_path / "downloads" / "download_manifest.json"
    assert manifest.is_file()


@pytest.mark.asyncio
async def test_browser_session_history_persists_bounded_masked_evidence(tmp_path):
    cfg = AppConfig()
    cfg.browser_use.history_dir = "history"
    cfg.browser_use.max_history_entries = 10
    session = BrowserSession(cfg, tmp_path)
    for idx in range(12):
        await session._capture_browser_session_history("unit", {"index": idx})
    assert len(session.browser_session_history) == 10
    history = json.loads((tmp_path / "history" / "session_history.json").read_text(encoding="utf-8"))
    assert len(history["history"]) == 10
    assert history["history"][-1]["extra"]["index"] == 11


def test_both_uis_expose_pause_resume_human_in_loop_controls():
    dashboard = (ROOT / "hip_id_agent" / "streamlit_dashboard.py").read_text(encoding="utf-8")
    platform = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    backend = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    assert '"Pause agent"' in dashboard and '"Resume agent"' in dashboard
    assert 'button("Pause"' in platform and 'button("Resume"' in platform
    assert '"/api/discovery/pause"' in backend and '"/api/discovery/resume"' in backend


def test_browser_use_remains_non_mutating_governed_augmentation():
    bridge = (ROOT / "hip_id_agent" / "browser_use_bridge.py").read_text(encoding="utf-8")
    session = (ROOT / "hip_id_agent" / "browser_session.py").read_text(encoding="utf-8")
    assert "does not run a free-form" in bridge and "not authorized to save/create/delete/deploy/migrate" in bridge
    assert "Fail-open means fall back to the normal managed persistent Chrome" in session
    assert "never to an ungoverned Browser-Use Agent" in session


def test_mcp_auto_runner_supports_enterprise_npx_then_bun_fallback(monkeypatch):
    from hip_id_agent import mcp_stdio

    monkeypatch.setattr(mcp_stdio.os, "name", "nt", raising=False)
    monkeypatch.setattr(mcp_stdio.shutil, "which", lambda name: r"C:\\Program Files\\nodejs\\npx.cmd" if name == "npx.cmd" else None)
    assert mcp_stdio.resolve_mcp_command("auto").lower().endswith("npx.cmd")
    assert mcp_stdio.resolve_mcp_command("bunx") == "bunx"
