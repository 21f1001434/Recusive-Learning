from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hip_id_agent.phase_form_entry import ensure_phase_form_entry
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


class _MCP:
    def __init__(self):
        self.queries = []

    async def find(self, *, text=None, regex=None):
        self.queries.append(text or regex)
        return {"text": f"button {text} [ref=e1]"}


class _Page:
    def __init__(self):
        self.url = "https://example.test/list"
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
        self.clicks = []

    async def _react_ensure_target_surface(self, url, max_steps=3):
        self.route_calls.append((url, max_steps))
        self.page.url = url
        return {"pass": True, "status": "target_surface"}

    async def navigate(self, url):
        self.page.url = url

    async def click_and_wait(self, *, action, locator, selector="", mutation_risk=None):
        self.clicks.append((action, selector))
        self.page.generation += 1
        self.page.form = True


@pytest.mark.asyncio
async def test_standard_phase_entry_clicks_top_level_add_and_verifies_form(tmp_path: Path):
    page = _Page(); browser = _Browser(page)

    async def find_add(p):
        return object()

    async def form_open(p):
        return p.form

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="data_map", listing_url=page.url,
        find_add=find_add, is_form_open=form_open, evidence_dir=tmp_path,
    )
    assert result["pass"] is True
    assert result["status"] == "form_open_after_add"
    assert browser.clicks
    assert "structural_opener" in browser.clicks[0][0]
    assert (tmp_path / "phase_form_entry_react.json").is_file()


@pytest.mark.asyncio
async def test_missing_add_routes_back_then_retries_autonomously(tmp_path: Path):
    page = _Page(); browser = _Browser(page)
    calls = {"find": 0}

    async def find_add(p):
        calls["find"] += 1
        return None if calls["find"] == 1 else object()

    async def form_open(p):
        return p.form

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="rule", listing_url="https://example.test/list",
        find_add=find_add, is_form_open=form_open, evidence_dir=tmp_path,
    )
    assert result["pass"] is True
    assert browser.route_calls
    assert calls["find"] >= 2
    assert browser.playwright_mcp_backend.queries.count("+ Add") >= 1


@pytest.mark.asyncio
async def test_bizflow_add_then_card_link_opens_multitab_form(tmp_path: Path):
    page = _Page(); browser = _Browser(page)

    async def find_add(p):
        return object()

    async def click_add(loc, step):
        page.intermediate = True
        page.generation += 1
        return True

    async def form_open(p):
        return p.form

    async def intermediate(p):
        return p.intermediate

    async def launch_card(p):
        p.form = True
        p.intermediate = False
        p.generation += 1
        return {"clicked": True, "method": "direct_template_card_link"}

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="biz_flow", listing_url=page.url,
        find_add=find_add, is_form_open=form_open, click_add=click_add,
        after_add=launch_card, is_intermediate_surface=intermediate,
        evidence_dir=tmp_path, max_steps=3,
    )
    assert result["pass"] is True
    assert result["status"] == "form_open_after_add_and_intermediate"
    assert any((step.get("after_add") or {}).get("method") == "direct_template_card_link" for step in result["steps"])


@pytest.mark.asyncio
async def test_bizflow_partial_template_surface_continues_without_second_add(tmp_path: Path):
    page = _Page(); page.intermediate = True
    browser = _Browser(page)
    add_calls = {"n": 0}

    async def find_add(p):
        add_calls["n"] += 1
        return object()

    async def form_open(p):
        return p.form

    async def intermediate(p):
        return p.intermediate

    async def launch_card(p):
        p.form = True; p.intermediate = False
        return {"clicked": True}

    result = await ensure_phase_form_entry(
        page=page, browser=browser, phase="biz_flow", listing_url=page.url,
        find_add=find_add, is_form_open=form_open,
        after_add=launch_card, is_intermediate_surface=intermediate,
        evidence_dir=tmp_path,
    )
    assert result["pass"] is True
    assert result["status"] == "form_open_after_intermediate_launch"
    assert add_calls["n"] == 0


@pytest.mark.asyncio
async def test_form_entry_failure_raises_recoverable_error_instead_of_empty_form(tmp_path: Path):
    page = _Page(); browser = _Browser(page)

    async def find_add(p):
        return None

    async def form_open(p):
        return False

    with pytest.raises(RuntimeError, match="HIP_FORM_ENTRY_NOT_OPENED"):
        await ensure_phase_form_entry(
            page=page, browser=browser, phase="source_transport_profile", listing_url=page.url,
            find_add=find_add, is_form_open=form_open, evidence_dir=tmp_path, max_steps=2,
        )


def test_form_entry_failure_is_classified_for_react_self_heal():
    kind = RuntimeSelfHealController.classify_failure(
        "HIP_FORM_ENTRY_NOT_OPENED: active form surface lost after bounded ReAct opener"
    )
    assert kind == "active_surface_lost"


def test_all_primary_form_families_use_phase_entry_react_controller():
    import hip_id_agent.datamap_kb as dm
    import hip_id_agent.doctype_kb as dt
    import hip_id_agent.rules_kb as rules
    import hip_id_agent.transport_profile_kb as tp
    import hip_id_agent.bizflow_kb as bf

    for module in (dm, dt, rules, tp, bf):
        source = inspect.getsource(module)
        assert "ensure_phase_form_entry(" in source
    assert "direct_template_card_link" in inspect.getsource(bf._click_bizflow_template_link_after_add)


def test_page_add_actions_are_marked_structural_openers():
    import hip_id_agent.datamap_kb as dm
    import hip_id_agent.doctype_kb as dt
    import hip_id_agent.rules_kb as rules
    import hip_id_agent.transport_profile_kb as tp
    import hip_id_agent.bizflow_kb as bf

    assert "structural_opener click_add_datamap" in inspect.getsource(dm.DataMapKBFlow.run)
    assert "structural_opener click_add_doctype" in inspect.getsource(dt._click_add_doctype_with_overlay_recovery)
    assert "structural_opener click_add_rule" in inspect.getsource(rules._click_add_rule_with_overlay_recovery)
    assert "structural_opener click_add_transport_profile" in inspect.getsource(tp._click_add_transport_profile_with_overlay_recovery)
    assert "structural_opener click_add_bizflow" in inspect.getsource(bf.BizFlowKBFlow.run)

@pytest.mark.asyncio
async def test_bizflow_tab_react_retries_until_requested_tab_is_proven(monkeypatch):
    import hip_id_agent.bizflow_kb as bf

    state = {"active": "Flow Details", "clicks": 0}

    async def current(_page):
        return state["active"]

    async def click(_page, tab):
        state["clicks"] += 1
        if state["clicks"] >= 2:
            state["active"] = "Configure Source"
        return {"clicked": True, "tab": tab}

    async def dismiss(_page):
        return 0

    monkeypatch.setattr(bf, "_current_bizflow_tab", current)
    monkeypatch.setattr(bf, "_click_bizflow_tab", click)
    monkeypatch.setattr(bf, "_dismiss_bizflow_overlays", dismiss)

    class Backend:
        async def find(self, *, text=None, regex=None):
            return {"text": f"tab {text} [ref=e9]"}

    class Page:
        _hip_playwright_mcp_backend = Backend()
        async def wait_for_timeout(self, ms):
            return None

    result = await bf._ensure_bizflow_tab_open(Page(), "Source Details", max_steps=3)
    assert result["pass"] is True
    assert result["active_tab"] == "Configure Source"
    assert state["clicks"] == 2


@pytest.mark.asyncio
async def test_bizflow_tab_react_fails_closed_before_wrong_tab_fill(monkeypatch):
    import hip_id_agent.bizflow_kb as bf

    async def current(_page):
        return "Flow Details"

    async def click(_page, tab):
        return {"clicked": False, "tab": tab}

    async def dismiss(_page):
        return 0

    monkeypatch.setattr(bf, "_current_bizflow_tab", current)
    monkeypatch.setattr(bf, "_click_bizflow_tab", click)
    monkeypatch.setattr(bf, "_dismiss_bizflow_overlays", dismiss)

    class Page:
        _hip_playwright_mcp_backend = None
        async def wait_for_timeout(self, ms):
            return None

    with pytest.raises(RuntimeError, match="HIP_BIZFLOW_TAB_NOT_OPENED"):
        await bf._ensure_bizflow_tab_open(Page(), "Target Details", max_steps=2)


def test_release_version_is_224():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
