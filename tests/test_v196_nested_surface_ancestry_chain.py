from __future__ import annotations

import types
from pathlib import Path

import pytest

import hip_id_agent.browser_session as browser_session_module
from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.semantic_affordance import (
    bind_child_affordance_surface,
    prefers_active_surface_chain,
    status,
)


def _surface(selector: str, *, sid: str, kind: str = "dialog", actions=None, z: int = 1200):
    return {
        "selector": selector,
        "id": sid,
        "kind": kind,
        "role": "dialog" if kind == "dialog" else kind,
        "aria_label": sid.replace("-", " "),
        "title": "",
        "actions": list(actions or ["Next", "Save", "Close"]),
        "item_count": len(list(actions or ["Next", "Save", "Close"])),
        "x": 100,
        "y": 100,
        "width": 480,
        "height": 420,
        "z_index": z,
    }


def test_child_surface_inherits_bounded_parent_provenance():
    parent = {
        "selector": "#deploy-menu",
        "id": "deploy-menu",
        "kind": "menu",
        "role": "menu",
        "chain_depth": 1,
        "ancestry": [],
        "binding_confidence": 100,
    }
    before = {"surfaces": [_surface("#deploy-menu", sid="deploy-menu", kind="menu", actions=["Deploy"])]}
    after = {
        "surfaces": [
            _surface("#deploy-menu", sid="deploy-menu", kind="menu", actions=["Deploy"]),
            _surface("#deploy-confirm", sid="deploy-confirm", kind="dialog", actions=["Continue", "Close"], z=1400),
        ]
    }
    bound = bind_child_affordance_surface(
        parent_proof=parent,
        before=before,
        after=after,
        opener_candidate={"aria_controls": "deploy-confirm"},
    )
    assert bound["bound"] is True
    proof = bound["surface_proof"]
    assert proof["selector"] == "#deploy-confirm"
    assert proof["chain_depth"] == 2
    assert proof["parent_id"] == "deploy-menu"
    assert proof["inherited_provenance"] is True
    assert proof["ancestry"][-1]["id"] == "deploy-menu"
    runtime = status()
    assert runtime["nested_surface_ancestry_chain"] is True
    assert runtime["child_surface_provenance_inheritance"] is True
    assert prefers_active_surface_chain("save") is True
    assert prefers_active_surface_chain("add_row") is False


@pytest.mark.asyncio
async def test_closed_child_surface_prunes_back_to_proven_parent(monkeypatch, tmp_path: Path):
    session = BrowserSession.__new__(BrowserSession)
    session.page = object()
    session.run_dir = tmp_path
    session.action_events = []
    parent = {"selector": "#parent", "id": "parent", "kind": "dialog", "role": "dialog", "chain_depth": 1}
    child = {"selector": "#child", "id": "child", "kind": "dialog", "role": "dialog", "chain_depth": 2}
    session._semantic_surface_chain = [parent, child]

    calls = []

    async def fake_refresh(page, proof):
        calls.append(proof["id"])
        if proof["id"] == "child":
            return {"rebound": False, "reason": "child closed"}
        return {"rebound": True, "selector": "#parent-new", "score": 120,
                "surface_proof": {**parent, "selector": "#parent-new", "last_rebind_score": 120}}

    monkeypatch.setattr(browser_session_module, "refresh_affordance_surface_lease", fake_refresh)
    active = await session._active_semantic_surface_lease()
    assert active["active"] is True
    assert active["selector"] == "#parent-new"
    assert calls == ["child", "parent"]
    assert len(session._semantic_surface_chain) == 1
    assert session._semantic_surface_chain[0]["id"] == "parent"


@pytest.mark.asyncio
async def test_active_child_blocks_global_same_named_fallback(monkeypatch, tmp_path: Path):
    session = BrowserSession.__new__(BrowserSession)
    session.page = object()
    session.run_dir = tmp_path
    session.action_events = []
    proof = {"selector": "#confirm", "id": "confirm", "kind": "dialog", "role": "dialog", "chain_depth": 1}
    session._semantic_surface_chain = [proof]

    async def fake_refresh(page, p):
        return {"rebound": True, "selector": "#confirm", "score": 140, "surface_proof": proof}

    async def fake_surface_resolve(page, **kwargs):
        assert kwargs["within_surface_selector"] if "within_surface_selector" in kwargs else True
        return {"resolved": False, "intent": "create", "reason": "Create not present in proven confirmation dialog"}

    global_calls = []

    async def fake_global(self, **kwargs):
        global_calls.append(kwargs)
        return {"resolved": True, "intent": "create", "selector": "#global-create", "label": "Create"}

    session.resolve_semantic_affordance = types.MethodType(fake_global, session)
    monkeypatch.setattr(browser_session_module, "refresh_affordance_surface_lease", fake_refresh)
    monkeypatch.setattr(browser_session_module, "resolve_affordance_in_proven_surface", fake_surface_resolve)

    with pytest.raises(RuntimeError, match="HIP_ACTIVE_SURFACE_CONTINUATION_UNRESOLVED"):
        await session.click_semantic_affordance(intent="create", aliases=["UHAL"], allow_mutation=True)
    assert global_calls == []


@pytest.mark.asyncio
async def test_click_from_proven_parent_pushes_new_child_surface(monkeypatch, tmp_path: Path):
    session = BrowserSession.__new__(BrowserSession)
    session.run_dir = tmp_path
    session.action_events = []
    parent = {
        "selector": "#edit-dialog", "id": "edit-dialog", "kind": "dialog", "role": "dialog",
        "chain_depth": 1, "action_seed": ["Next", "Close"], "opener_control_ids": ["edit-dialog"],
        "lease_revalidated": True,
    }
    session._semantic_surface_chain = [parent]

    class Locator:
        @property
        def first(self):
            return self

    class Page:
        def locator(self, selector):
            return Locator()

    session.page = Page()

    async def fake_refresh(page, p):
        return {"rebound": True, "selector": "#edit-dialog", "score": 140,
                "surface_proof": {**parent, "selector": "#edit-dialog", "last_rebind_score": 140}}

    surface_results = [
        {"resolved": True, "intent": "next", "selector": "#edit-dialog .next", "label": "Next",
         "expected_effect": "surface_advanced", "candidate": {"aria_controls": "child-picker"},
         "surface_provenance": {**parent, "selector": "#edit-dialog", "lease_revalidated": True}},
        {"resolved": True, "intent": "next", "selector": "#edit-dialog .next", "label": "Next",
         "expected_effect": "surface_advanced", "candidate": {"aria_controls": "child-picker"},
         "surface_provenance": {**parent, "selector": "#edit-dialog", "lease_revalidated": True}},
    ]

    async def fake_surface_resolve(page, **kwargs):
        return surface_results.pop(0)

    async def fake_membership(page, **kwargs):
        return {"pass": True, "reason": "target_inside_proven_surface"}

    clicks = []

    async def fake_click(self, *, action, locator, selector="", screenshot_name=None):
        clicks.append((action, selector))

    before = {
        "url": "u", "headings": ["Edit"], "dialogs": ["Edit"], "drawers": [], "menus": [], "expanded": 0,
        "surfaces": [_surface("#edit-dialog", sid="edit-dialog", kind="dialog", actions=["Next", "Close"])],
    }
    after = {
        "url": "u", "headings": ["Edit"], "dialogs": ["Edit", "Choose Target"], "drawers": [], "menus": [], "expanded": 0,
        "surfaces": [
            _surface("#edit-dialog", sid="edit-dialog", kind="dialog", actions=["Next", "Close"]),
            _surface("#child-picker", sid="child-picker", kind="dialog", actions=["Save", "Close"], z=1500),
        ],
    }
    snapshots = [before, after]

    async def fake_snapshot(page, *, selector=""):
        return snapshots.pop(0)

    session.click_and_wait = types.MethodType(fake_click, session)
    monkeypatch.setattr(browser_session_module, "refresh_affordance_surface_lease", fake_refresh)
    monkeypatch.setattr(browser_session_module, "resolve_affordance_in_proven_surface", fake_surface_resolve)
    monkeypatch.setattr(browser_session_module, "verify_affordance_target_membership", fake_membership)
    monkeypatch.setattr(browser_session_module, "snapshot_affordance_surface", fake_snapshot)

    evidence = await session.click_semantic_affordance(
        intent="next", aliases=["UHAL"], allow_mutation=False, action_label="Next", require_effect=True,
    )
    assert evidence["effect"]["pass"] is True
    assert evidence["child_surface_binding"]["bound"] is True
    assert evidence["child_surface_binding"]["surface_proof"]["parent_id"] == "edit-dialog"
    assert len(session._semantic_surface_chain) == 2
    assert session._semantic_surface_chain[-1]["id"] == "child-picker"
    assert session._semantic_surface_chain[-1]["chain_depth"] == 2
    assert clicks == [("Next", "#edit-dialog .next")]
