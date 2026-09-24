from __future__ import annotations

import types

import pytest

import hip_id_agent.browser_session as browser_session_module
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.semantic_affordance import (
    requires_compound_menu_fallback,
    selector_looks_generation_volatile,
    status,
    verify_affordance_effect,
)


def test_row_actions_support_compound_overflow_menu_fallback():
    assert requires_compound_menu_fallback("edit") is True
    assert requires_compound_menu_fallback("clone") is True
    assert requires_compound_menu_fallback("deploy") is True
    assert requires_compound_menu_fallback("expand") is False
    runtime = status()
    assert runtime["generation_safe_reresolution"] is True
    assert runtime["volatile_dynamic_id_avoidance"] is True
    assert "edit" in runtime["compound_menu_intents"]


def test_generation_volatile_selectors_are_detected():
    assert selector_looks_generation_volatile("button#dds-generated-12345") is True
    assert selector_looks_generation_volatile("div#ng-accordion-99881 > button") is True
    assert selector_looks_generation_volatile("button#edit-rule") is False
    assert selector_looks_generation_volatile('button[data-testid="edit-rule"]') is False


def test_effect_verifier_does_not_accept_preexisting_action_text():
    before = {
        "url": "https://hip/rules",
        "headings": ["Rules", "Edit"],
        "dialogs": [], "drawers": [], "menus": [], "expanded": 0,
    }
    after_same = dict(before)
    result = verify_affordance_effect(
        intent="edit", expected_effect="edit_surface_opened", before=before, after=after_same
    )
    assert result["pass"] is False

    after_open = {
        **before,
        "dialogs": ["Edit Rule UHAL"],
    }
    result = verify_affordance_effect(
        intent="edit", expected_effect="edit_surface_opened", before=before, after=after_open
    )
    assert result["pass"] is True


@pytest.mark.asyncio
async def test_browser_session_opens_scoped_menu_then_reresolves_target(monkeypatch, tmp_path):
    session = object.__new__(BrowserSession)
    session.page = types.SimpleNamespace(locator=lambda selector: types.SimpleNamespace(first=object()))
    session.run_dir = tmp_path
    session.action_events = []

    resolutions = [
        # direct target unavailable before overflow menu opens
        {"resolved": False, "intent": "edit", "reason": "hidden"},
        # resolve More Actions, then just-in-time re-resolve it
        {"resolved": True, "intent": "more_actions", "selector": "#row-menu", "label": "More", "alias_hits": ["uhal"], "expected_effect": "menu_or_popover_opened"},
        {"resolved": True, "intent": "more_actions", "selector": "#row-menu-fresh", "label": "More", "alias_hits": ["uhal"], "expected_effect": "menu_or_popover_opened"},
        # target becomes visible after menu opens, then is re-resolved before click
        {"resolved": True, "intent": "edit", "selector": "#edit-old", "label": "Edit", "alias_hits": ["uhal"], "expected_effect": "edit_surface_opened"},
        {"resolved": True, "intent": "edit", "selector": "#edit-fresh", "label": "Edit", "alias_hits": ["uhal"], "expected_effect": "edit_surface_opened"},
    ]

    async def fake_resolve(self, **kwargs):
        return resolutions.pop(0)

    clicks = []
    async def fake_click(self, *, action, locator, selector="", screenshot_name=None):
        clicks.append((action, selector))

    snapshots = [
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": [], "expanded": 0},
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": ["Edit Clone Deploy"], "expanded": 0},
        {"url": "u", "headings": ["Rules"], "dialogs": [], "drawers": [], "menus": ["Edit Clone Deploy"], "expanded": 0},
        {"url": "u", "headings": ["Rules"], "dialogs": ["Edit UHAL Rule"], "drawers": [], "menus": [], "expanded": 0},
    ]
    async def fake_snapshot(page, *, selector=""):
        return snapshots.pop(0)

    session.resolve_semantic_affordance = types.MethodType(fake_resolve, session)
    session.click_and_wait = types.MethodType(fake_click, session)
    monkeypatch.setattr(browser_session_module, "snapshot_affordance_surface", fake_snapshot)

    evidence = await session.click_semantic_affordance(
        intent="edit", aliases=["UHAL", "Rules"], allow_mutation=False,
        action_label="Edit", require_effect=True, allow_compound_menu=True,
    )

    assert evidence["effect"]["pass"] is True
    assert evidence["generation_safe_reresolution"] is True
    assert evidence["compound_menu"]["menu_effect"]["pass"] is True
    assert clicks == [("More", "#row-menu-fresh"), ("Edit", "#edit-fresh")]
