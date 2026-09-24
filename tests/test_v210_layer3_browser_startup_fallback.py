from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config


def test_browser_candidates_are_edge_then_chrome_then_playwright_chromium(tmp_path: Path):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    cfg.portal.chromium_channel = "msedge"
    cfg.portal.allow_browser_fallback = True
    session = BrowserSession(cfg, tmp_path / "run")
    candidates = session._managed_browser_candidates()
    assert [x["name"] for x in candidates] == ["edge", "chrome", "playwright_chromium"]
    assert candidates[0]["channel"] == "msedge"
    assert candidates[1]["channel"] == "chrome"
    assert candidates[0]["profile"] != candidates[1]["profile"] != candidates[2]["profile"]


class FakeContext:
    def __init__(self, name: str):
        self.name = name
        self.closed = False
    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.calls = []
        self.contexts = []
    async def launch_persistent_context(self, **kwargs):
        self.calls.append(dict(kwargs))
        channel = kwargs.get("channel") or "playwright_chromium"
        if channel == "msedge":
            raise RuntimeError("Edge launch policy failure")
        ctx = FakeContext(channel)
        self.contexts.append(ctx)
        return ctx


@pytest.mark.asyncio
async def test_explicit_edge_launch_failure_falls_back_to_chrome_before_mission_lock(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.chromium_channel = "msedge"
    cfg.portal.browser_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    session = BrowserSession(cfg, tmp_path / "run")
    fake = FakeChromium()
    session._playwright = SimpleNamespace(chromium=fake)
    monkeypatch.setattr(session, "_wait_for_cdp_ready", lambda timeout_seconds=8.0: asyncio.sleep(0, result={"ok": True}))

    ctx = await session._launch_managed_context_with_fallback(
        common_kwargs={"headless": False, "args": ["--remote-debugging-port=9237"]},
        needs_local_cdp=True,
    )
    assert ctx.name == "chrome"
    assert session._selected_browser["browser"] == "chrome"
    assert session._selected_browser["fallback_used"] is True
    assert [a["status"] for a in session._browser_launch_attempts] == ["failed", "selected"]


@pytest.mark.asyncio
async def test_explicit_unhealthy_edge_cdp_is_closed_then_chrome_is_selected(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.chromium_channel = "msedge"
    cfg.portal.browser_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.fallback_to_playwright_chromium = False
    session = BrowserSession(cfg, tmp_path / "run")

    class AllLaunchChromium:
        def __init__(self): self.contexts=[]
        async def launch_persistent_context(self, **kwargs):
            ctx=FakeContext(kwargs.get("channel") or "chromium")
            self.contexts.append(ctx); return ctx
    fake=AllLaunchChromium(); session._playwright=SimpleNamespace(chromium=fake)
    health = iter([{"ok": False, "error": "ws unavailable"}, {"ok": True, "browser": "Chrome"}])
    async def probe(timeout_seconds=8.0): return next(health)
    monkeypatch.setattr(session, "_wait_for_cdp_ready", probe)

    ctx=await session._launch_managed_context_with_fallback(common_kwargs={"args": []}, needs_local_cdp=True)
    assert fake.contexts[0].closed is True
    assert ctx is fake.contexts[1]
    assert session._selected_browser["browser"] == "chrome"


@pytest.mark.asyncio
async def test_browser_switch_is_prohibited_after_mission_browser_is_locked(tmp_path: Path):
    session=BrowserSession(AppConfig(), tmp_path / "run")
    session._mission_browser_locked=True
    with pytest.raises(RuntimeError, match="HIP_BROWSER_SWITCH_PROHIBITED"):
        await session._launch_managed_context_with_fallback(common_kwargs={"args": []}, needs_local_cdp=False)


@pytest.mark.asyncio
async def test_browser_use_failure_detaches_and_enters_cooldown_instead_of_reconnect_loop(tmp_path: Path):
    cfg=AppConfig(); cfg.browser_use.snapshot_timeout_seconds=0.05
    session=BrowserSession(cfg, tmp_path / "run")

    class BrokenBridge:
        def __init__(self): self.stopped=0
        async def recovery_context(self, max_elements=120):
            raise RuntimeError("CDP WebSocket message handler exited unexpectedly")
        async def stop(self): self.stopped += 1
    bridge=BrokenBridge(); session.browser_use_bridge=bridge
    session.browser_use_capabilities={"attached": True}
    result=await session.browser_use_recovery_context()
    assert result["available"] is False
    assert "detached" in result["reason"]
    assert bridge.stopped == 1
    assert session.browser_use_capabilities["attached"] is False
    assert session._recovery_intelligence_cooldown_until > asyncio.get_running_loop().time()


def test_shipped_config_uses_chrome_primary_with_startup_only_edge_fallback():
    cfg=load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert cfg.portal.chromium_channel == "chrome"
    assert cfg.portal.allow_browser_fallback is True
    assert cfg.portal.fallback_to_edge is True
    assert cfg.portal.fallback_to_playwright_chromium is True
