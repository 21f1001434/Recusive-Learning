from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent import dummy_fill_e2e


DATAMAPS = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
DOCTYPES = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"


class _Body:
    def __init__(self, page):
        self.page = page

    async def inner_text(self, timeout=0):
        return self.page.body


class _Page:
    def __init__(self, url=DATAMAPS, body="Developer SecureLink Data Map Map Identifier Mapping authenticated"):
        self.url = url
        self.body = body

    def is_closed(self):
        return False

    def locator(self, selector):
        if selector == "body":
            return _Body(self)
        raise AssertionError(selector)

    async def evaluate(self, script, *args):
        if "document.readyState" in script:
            return "complete"
        if "window.location.assign" in script and args:
            self.url = args[0]
            self.body = "Developer SecureLink Document Type Document Identifier"
        return None


class _Chrome:
    def __init__(self, page):
        self.page = page

    async def select_page_for_url(self, expected_url):
        return {
            "pass": True,
            "selected": {"page_id": 1, "url": self.page.url},
            "pages": [{"page_id": 1, "url": self.page.url}],
        }


class _PlaywrightMCP:
    def __init__(self, page):
        self.page = page

    async def get_current_url(self):
        return self.page.url


@pytest.mark.asyncio
async def test_wrong_authenticated_module_is_not_accepted_as_target(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    session.page = _Page(
        DATAMAPS,
        "Developer SecureLink authenticated portal Data Map Mapping Document Type menu",
    )
    assert await session._navigation_page_is_usable(DOCTYPES) is False
    assert await session._navigation_page_is_usable(DATAMAPS) is True


@pytest.mark.asyncio
async def test_react_replans_when_first_route_action_leaves_browser_on_old_module(tmp_path: Path, monkeypatch):
    session = BrowserSession(AppConfig(), tmp_path)
    page = _Page()
    session.page = page
    session._authenticated_once = True
    session.mcp_backend = _Chrome(page)
    session.playwright_mcp_backend = _PlaywrightMCP(page)

    async def logged_in(_page):
        return True

    async def noop(*args, **kwargs):
        return {}

    calls = []

    async def navigate(url):
        calls.append(url)
        # First action is a no-op, reproducing run 013312. The next ReAct action
        # observes the unchanged route and retries inside the same session.
        if len(calls) >= 2:
            page.url = url
            page.body = "Developer SecureLink Document Type Document Identifier"

    monkeypatch.setattr(session, "_looks_logged_in", logged_in)
    monkeypatch.setattr(session, "_consolidate_session_pages", noop)
    monkeypatch.setattr(session, "_dismiss_transient_ui", noop)
    monkeypatch.setattr(session, "navigate", navigate)

    await session.goto_base_and_complete_sso(DOCTYPES)

    assert len(calls) == 2
    assert page.url == DOCTYPES
    assert session._sso_prompt_count == 0
    trace = json.loads((tmp_path / "mcp_runtime" / "navigation_react_trace.json").read_text())
    assert trace["pass"] is True
    assert trace["steps"][0]["judge"]["target_committed"] is False
    assert trace["steps"][-1]["plan"]["action"] == "accept_target"
    assert "validated HIP module surface contract" in trace["knowledge_sources"]


@pytest.mark.asyncio
async def test_dual_mcp_gate_separates_actual_agreement_from_target_match(tmp_path: Path):
    session = BrowserSession(AppConfig(), tmp_path)
    page = _Page(DATAMAPS)
    session.page = page
    session.mcp_backend = _Chrome(page)
    session.playwright_mcp_backend = _PlaywrightMCP(page)

    observation_gate = await session._verify_dual_mcp_same_surface(
        DOCTYPES,
        timeout_seconds=0,
        fail_closed=False,
        require_expected_target=False,
    )
    assert observation_gate["same_actual_surface"] is True
    assert observation_gate["target_match"] is False
    assert observation_gate["pass"] is True

    target_gate = await session._verify_dual_mcp_same_surface(
        DOCTYPES,
        timeout_seconds=0,
        fail_closed=False,
        require_expected_target=True,
    )
    assert target_gate["same_actual_surface"] is True
    assert target_gate["target_match"] is False
    assert target_gate["pass"] is False


def test_phase_recovery_imports_masking_helper_and_classifies_route_errors():
    source = inspect.getsource(dummy_fill_e2e)
    assert "mask_sensitive_data, mask_sensitive_string" in source
    assert '"HIP_ROUTE_NOT_COMMITTED" in message' in source
    assert '"HIP_MCP_SURFACE_DRIFT" in message' in source
    assert "recoverable_navigation" in source
