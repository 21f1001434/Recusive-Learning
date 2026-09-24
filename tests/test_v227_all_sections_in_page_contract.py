from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hip_id_agent.phase_form_entry import ensure_phase_form_entry, IN_PAGE_CREATE_PHASES
import hip_id_agent.datamap_kb as dm
import hip_id_agent.doctype_kb as dt
import hip_id_agent.rules_kb as rules
import hip_id_agent.transport_profile_kb as tp
import hip_id_agent.bizflow_kb as bf


class _MCP:
    async def find(self, *, text=None, regex=None):
        return {"text": f"{text or regex} [ref=e1]"}


class _Page:
    def __init__(self, url="https://example.test/list"):
        self.url = url
        self.form = False
        self.intermediate = False
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
        self.page.intermediate = False
        return {"pass": True}

    async def navigate(self, url):
        self.page.url = url


@pytest.mark.parametrize(
    "phase",
    ["data_map", "document_type", "source_document_type", "target_document_type", "rule",
     "transport_profile", "source_transport_profile", "target_transport_profile", "biz_flow"],
)
def test_all_create_phases_are_registered_as_in_page(phase):
    assert phase in IN_PAGE_CREATE_PHASES


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["rule", "document_type", "transport_profile"])
async def test_standard_sections_reject_separate_create_route_even_without_explicit_flag(tmp_path: Path, phase: str):
    listing = f"https://example.test/{phase}"
    page = _Page(listing)
    browser = _Browser(page)
    calls = {"n": 0}

    async def find_add(_page):
        return object()

    async def click_add(_loc, _step):
        calls["n"] += 1
        page.generation += 1
        if calls["n"] == 1:
            page.url = listing + "/create"
            page.form = True
        else:
            page.url = listing + "?drawer=create"
            page.form = True
        return True

    async def form_open(p):
        return p.form

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase=phase, listing_url=listing,
        find_add=find_add, is_form_open=form_open, click_add=click_add,
        evidence_dir=tmp_path / phase, max_steps=3,
    )
    assert result["pass"] is True
    assert result["require_same_route"] is True
    assert calls["n"] == 2
    assert browser.route_calls
    assert any((step.get("effect") or {}).get("wrong_route_after_add") for step in result["steps"])


@pytest.mark.asyncio
async def test_bizflow_template_launcher_must_also_stay_in_page(tmp_path: Path):
    listing = "https://example.test/bizflows"
    page = _Page(listing)
    browser = _Browser(page)
    add_calls = {"n": 0}
    launch_calls = {"n": 0}

    async def find_add(_page):
        return object()

    async def click_add(_loc, _step):
        add_calls["n"] += 1
        page.url = listing + "#templates"
        page.intermediate = True
        page.form = False
        page.generation += 1
        return True

    async def intermediate(p):
        return p.intermediate

    async def form_open(p):
        return p.form

    async def launch_card(p):
        launch_calls["n"] += 1
        p.generation += 1
        if launch_calls["n"] == 1:
            p.url = listing + "/create"
            p.form = True
            p.intermediate = False
        else:
            p.url = listing + "?wizard=create#flow-details"
            p.form = True
            p.intermediate = False
        return {"clicked": True}

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="biz_flow", listing_url=listing,
        find_add=find_add, is_form_open=form_open, click_add=click_add,
        after_add=launch_card, is_intermediate_surface=intermediate,
        evidence_dir=tmp_path, max_steps=4,
    )
    assert result["pass"] is True
    assert launch_calls["n"] == 2
    assert browser.route_calls
    assert any(
        (step.get("effect_after_intermediate") or {}).get("wrong_route_after_intermediate")
        or (step.get("effect") or {}).get("wrong_route_after_intermediate")
        for step in result["steps"]
    )


def test_all_runtime_call_sites_explicitly_enable_same_route():
    sources = {
        "data_map": inspect.getsource(dm.DataMapKBFlow.run),
        "document_type": inspect.getsource(dt.DocumentTypeKBFlow.run),
        "rule": inspect.getsource(rules.RuleKBFlow.run),
        "transport_profile": inspect.getsource(tp.TransportProfileKBFlow.run),
        "biz_flow": inspect.getsource(bf.BizFlowKBFlow.run),
    }
    for phase, source in sources.items():
        assert "ensure_phase_form_entry(" in source, phase
        call_tail = source[source.index("ensure_phase_form_entry("):]
        assert "require_same_route=True" in call_tail[:1200], phase


def test_rules_and_tp_use_shared_top_right_same_page_add_finder():
    assert "find_same_page_top_right_add(page)" in inspect.getsource(rules._find_add_button)
    assert "same_page_add_candidate(page, loc)" in inspect.getsource(rules._find_add_button)
    assert "find_same_page_top_right_add(page)" in inspect.getsource(tp._find_add_button)
    assert "same_page_add_candidate(page, loc)" in inspect.getsource(tp._find_add_button)


def test_doctype_and_bizflow_use_shared_top_right_same_page_add_finder():
    assert "find_same_page_top_right_add(page)" in inspect.getsource(dt._find_add_button)
    assert "same_page_add_candidate(page, loc)" in inspect.getsource(dt._find_add_button)
    assert "find_same_page_top_right_add(page)" in inspect.getsource(bf._find_bizflow_add_button)
    assert "same_page_add_candidate(page, loc)" in inspect.getsource(bf._find_bizflow_add_button)


def test_bizflow_direct_template_link_rejects_path_change():
    src = inspect.getsource(bf._click_bizflow_template_link_after_add)
    assert "target.origin!==window.location.origin" in src
    assert "target.pathname" in src
    assert "window.location.pathname" in src
