from __future__ import annotations

import types

import pytest

import hip_id_agent.browser_session as browser_session_module
import hip_id_agent.semantic_affordance as semantic_module
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.semantic_affordance import (
    bind_new_affordance_surface,
    resolve_affordance_in_proven_surface,
    status,
)


def test_new_detached_surface_binds_by_opener_aria_controls():
    before = {"surfaces": []}
    after = {
        "surfaces": [
            {"selector": "#menu-42", "id": "menu-42", "kind": "menu", "item_count": 4, "z_index": 1200}
        ]
    }
    bound = bind_new_affordance_surface(
        before=before,
        after=after,
        opener_candidate={"aria_controls": "menu-42"},
    )
    assert bound["bound"] is True
    assert bound["selector"] == "#menu-42"
    assert bound["reason"] == "opener_aria_controls"
    runtime = status()
    assert runtime["detached_overlay_provenance_binding"] is True
    assert runtime["virtualized_surface_scrolling"] is True
    assert runtime["surface_scoped_target_resolution"] is True


def test_multiple_equally_plausible_new_surfaces_fail_closed():
    before = {"surfaces": []}
    after = {
        "surfaces": [
            {"selector": "#menu-a", "id": "", "kind": "menu", "item_count": 3, "z_index": 1000},
            {"selector": "#menu-b", "id": "", "kind": "menu", "item_count": 3, "z_index": 1000},
        ]
    }
    bound = bind_new_affordance_surface(before=before, after=after, opener_candidate={})
    assert bound["bound"] is False
    assert bound["ambiguous"] is True


@pytest.mark.asyncio
async def test_virtualized_surface_scrolls_without_clicking_until_target_is_visible(monkeypatch):
    resolutions = [
        {"resolved": False, "intent": "deploy", "reason": "not rendered yet"},
        {"resolved": False, "intent": "deploy", "reason": "still below viewport"},
        {"resolved": True, "intent": "deploy", "selector": "#deploy", "label": "Deploy", "expected_effect": "mutation_surface_opened"},
    ]
    calls = []

    async def fake_resolve(page, **kwargs):
        calls.append(kwargs)
        return resolutions.pop(0)

    class FakePage:
        def __init__(self):
            self.scrolls = 0
        async def evaluate(self, script, args):
            self.scrolls += 1
            return {"moved": True, "before": (self.scrolls - 1) * 100, "after": self.scrolls * 100, "max": 500}
        async def wait_for_timeout(self, ms):
            return None

    monkeypatch.setattr(semantic_module, "resolve_semantic_affordance", fake_resolve)
    page = FakePage()
    result = await resolve_affordance_in_proven_surface(
        page, intent="deploy", surface_selector="#detached-menu", allow_mutation=True, max_scrolls=4
    )
    assert result["resolved"] is True
    assert result["virtualized_surface_scrolling"] is True
    assert result["surface_provenance"]["selector"] == "#detached-menu"
    assert page.scrolls == 2
    assert all(x["within_surface_selector"] == "#detached-menu" for x in calls)
    assert all(x["aliases"] == () for x in calls)


@pytest.mark.asyncio
async def test_compound_action_transfers_scope_to_new_detached_overlay(monkeypatch, tmp_path):
    session = object.__new__(BrowserSession)
    session.page = types.SimpleNamespace(locator=lambda selector: types.SimpleNamespace(first=object()))
    session.run_dir = tmp_path
    session.action_events = []

    resolutions = [
        {"resolved": False, "intent": "edit", "reason": "hidden behind menu"},
        {"resolved": True, "intent": "more_actions", "selector": "#row-more", "label": "More", "alias_hits": ["uhal"], "expected_effect": "menu_or_popover_opened", "candidate": {"aria_controls": "detached-actions"}},
        {"resolved": True, "intent": "more_actions", "selector": "#row-more-fresh", "label": "More", "alias_hits": ["uhal"], "expected_effect": "menu_or_popover_opened", "candidate": {"aria_controls": "detached-actions"}},
    ]

    async def fake_session_resolve(self, **kwargs):
        return resolutions.pop(0)

    clicks = []
    async def fake_click(self, *, action, locator, selector="", screenshot_name=None):
        clicks.append((action, selector))

    snapshots = [
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": [], "expanded": 0, "surfaces": []},
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": ["Edit Clone Deploy"], "expanded": 0,
         "surfaces": [{"selector": "#detached-actions", "id": "detached-actions", "kind": "menu", "item_count": 3, "z_index": 1200}]},
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": ["Edit Clone Deploy"], "expanded": 0, "surfaces": []},
        {"url": "u", "headings": ["Rules"], "dialogs": ["Edit UHAL Rule"], "drawers": [], "menus": [], "expanded": 0, "surfaces": []},
    ]
    async def fake_snapshot(page, *, selector=""):
        return snapshots.pop(0)

    surface_resolutions = [
        {"resolved": True, "intent": "edit", "selector": "#detached-actions > #edit-old", "label": "Edit", "expected_effect": "edit_surface_opened", "alias_hits": [], "surface_provenance": {"selector": "#detached-actions", "scroll_attempts": 0}},
        {"resolved": True, "intent": "edit", "selector": "#detached-actions > #edit-fresh", "label": "Edit", "expected_effect": "edit_surface_opened", "alias_hits": [], "surface_provenance": {"selector": "#detached-actions", "scroll_attempts": 0}},
    ]
    async def fake_surface_resolve(page, **kwargs):
        assert kwargs["surface_selector"] == "#detached-actions"
        return surface_resolutions.pop(0)

    session.resolve_semantic_affordance = types.MethodType(fake_session_resolve, session)
    session.click_and_wait = types.MethodType(fake_click, session)
    monkeypatch.setattr(browser_session_module, "snapshot_affordance_surface", fake_snapshot)
    monkeypatch.setattr(browser_session_module, "resolve_affordance_in_proven_surface", fake_surface_resolve)

    evidence = await session.click_semantic_affordance(
        intent="edit", aliases=["UHAL", "Rules"], allow_mutation=False,
        action_label="Edit", require_effect=True, allow_compound_menu=True,
    )

    assert evidence["effect"]["pass"] is True
    assert evidence["detached_overlay_provenance"] is True
    assert evidence["compound_menu"]["surface_binding"]["bound"] is True
    assert clicks == [("More", "#row-more-fresh"), ("Edit", "#detached-actions > #edit-fresh")]
