from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hip_id_agent.phase_form_entry import ensure_phase_form_entry
import hip_id_agent.datamap_kb as dm
import hip_id_agent.dds_control_driver as dds


class _MCP:
    async def find(self, *, text=None, regex=None):
        return {"text": f"{text or regex} [ref=e1]"}


class _Page:
    def __init__(self):
        self.url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
        self.form = False
        self.generation = 0

    async def evaluate(self, script, *args):
        if "HIP_DOM_GENERATION" in script or "HIP_DOM_EVENT_SEQ" in script:
            return self.generation
        return 0

    async def wait_for_timeout(self, ms):
        return None


class _Browser:
    def __init__(self, page):
        self.page = page
        self.playwright_mcp_backend = _MCP()
        self.route_calls = []

    async def _react_ensure_target_surface(self, url, max_steps=3):
        self.route_calls.append(url)
        self.page.url = url
        self.page.form = False
        return {"pass": True, "status": "restored_listing"}

    async def navigate(self, url):
        self.page.url = url


@pytest.mark.asyncio
async def test_same_route_form_entry_rejects_wrong_navigating_add_then_recovers(tmp_path: Path):
    page = _Page(); browser = _Browser(page)
    calls = {"click": 0}

    async def find_add(_page):
        return object()

    async def click_add(_loc, _step):
        calls["click"] += 1
        page.generation += 1
        if calls["click"] == 1:
            page.url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps/create"
            page.form = True  # must NOT be accepted because Data Map is in-page
        else:
            page.url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps#drawer=create"
            page.form = True
        return True

    async def form_open(p):
        return p.form

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="data_map",
        listing_url="https://developer.dell.com/hybrid-integrations/securelink/datamaps",
        find_add=find_add, is_form_open=form_open, click_add=click_add,
        evidence_dir=tmp_path, max_steps=3, require_same_route=True,
    )
    assert result["pass"] is True
    assert calls["click"] == 2
    assert browser.route_calls, "wrong route must be restored to the listing"
    assert any((step.get("effect") or {}).get("wrong_route_after_add") for step in result["steps"])


@pytest.mark.asyncio
async def test_same_route_policy_allows_query_or_hash_in_page_state(tmp_path: Path):
    page = _Page(); browser = _Browser(page)

    async def find_add(_page):
        return object()

    async def click_add(_loc, _step):
        page.url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps?drawer=create#map"
        page.form = True
        return True

    async def form_open(p):
        return p.form

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="data_map", listing_url=page.url,
        find_add=find_add, is_form_open=form_open, click_add=click_add,
        evidence_dir=tmp_path, require_same_route=True,
    )
    assert result["pass"] is True
    assert not browser.route_calls


@pytest.mark.asyncio
async def test_datamap_form_open_proof_accepts_progressive_initial_drawer(monkeypatch):
    class P:
        url = dm.DATAMAPS_URL

    async def root_info(_page, _phase):
        return {
            "text": "Create Map  Map Identifier  Map Name  Cancel",
            "controls": 2,
            "selector": ".create-map-drawer",
        }

    monkeypatch.setattr(dm, "active_form_root_info", root_info)
    assert await dm._looks_like_datamap_add_form(P(), dm.DATAMAPS_URL) is True


@pytest.mark.asyncio
async def test_datamap_form_open_proof_rejects_separate_create_route(monkeypatch):
    class P:
        url = dm.DATAMAPS_URL + "/create"

    async def root_info(_page, _phase):
        return {"text": "Create Map Map Identifier Map Name", "controls": 4}

    monkeypatch.setattr(dm, "active_form_root_info", root_info)
    assert await dm._looks_like_datamap_add_form(P(), dm.DATAMAPS_URL) is False


@pytest.mark.asyncio
async def test_datamap_active_surface_does_not_require_late_upload_fields(monkeypatch):
    async def active_text(_page, _phase):
        return "Create Map Map Identifier Map Name Contivo Version"

    monkeypatch.setattr(dds, "active_form_text", active_text)
    result = await dds.assert_active_surface(object(), "data_map")
    assert result["fatal"] == []


def test_datamap_run_has_single_react_owned_add_transaction_and_same_route_policy():
    src = inspect.getsource(dm.DataMapKBFlow.run)
    assert "require_same_route=True" in src
    assert "Data Maps top-right + Add" in src
    assert "datamap_in_page_create_form_opened" in src
    assert "add = await _find_add_button(page)" not in src


def test_datamap_add_finder_rejects_route_changing_anchor_and_prefers_top_right():
    src = inspect.getsource(dm._find_add_button)
    assert "target_path.lower()!=current_path.lower()" in src
    assert "page-level Data Maps + Add" in src
    assert "top-right" in src


def test_release_version_is_v227():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
