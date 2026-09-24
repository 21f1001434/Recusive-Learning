from __future__ import annotations

import pytest

import hip_id_agent.semantic_affordance as semantic_module
from hip_id_agent.semantic_affordance import (
    rebind_proven_affordance_surface,
    resolve_affordance_in_proven_surface,
    verify_affordance_target_membership,
    status,
)


def _surface(selector: str, *, sid: str = "", x: int = 100, actions=None):
    return {
        "selector": selector,
        "id": sid,
        "kind": "menu",
        "role": "menu",
        "aria_label": "Row actions",
        "title": "",
        "actions": list(actions or ["Edit", "Clone", "Deploy"]),
        "item_count": 3,
        "x": x,
        "y": 120,
        "width": 220,
        "height": 260,
        "z_index": 1200,
    }


def test_surface_lease_rebinds_recreated_overlay_by_opener_control_id():
    proof = {
        "selector": "body > div:nth-of-type(2)",
        "id": "actions-42",
        "kind": "menu",
        "role": "menu",
        "aria_label": "Row actions",
        "x": 100,
        "y": 120,
        "width": 220,
        "height": 260,
        "action_seed": ["Edit", "Clone", "Deploy"],
        "opener_control_ids": ["actions-42"],
    }
    snapshot = {"surfaces": [_surface("body > div:nth-of-type(5)", sid="actions-42", x=103)]}
    rebound = rebind_proven_affordance_surface(proof=proof, snapshot=snapshot)
    assert rebound["rebound"] is True
    assert rebound["selector"] == "body > div:nth-of-type(5)"
    assert "opener_controlled_id" in rebound["evidence"]
    assert rebound["surface_proof"]["selector"] == "body > div:nth-of-type(5)"


def test_surface_lease_fails_closed_for_two_structurally_equal_replacements():
    proof = {
        "selector": "body > div:nth-of-type(2)",
        "kind": "menu",
        "role": "menu",
        "aria_label": "Row actions",
        "x": 100,
        "y": 120,
        "width": 220,
        "height": 260,
        "action_seed": ["Edit", "Clone", "Deploy"],
        "opener_control_ids": [],
    }
    snapshot = {
        "surfaces": [
            _surface("body > div:nth-of-type(7)", x=100),
            _surface("body > div:nth-of-type(8)", x=100),
        ]
    }
    rebound = rebind_proven_affordance_surface(proof=proof, snapshot=snapshot)
    assert rebound["rebound"] is False
    assert rebound["ambiguous"] is True
    runtime = status()
    assert runtime["surface_continuity_lease"] is True
    assert runtime["stale_duplicate_surface_guard"] is True
    assert runtime["stable_target_double_read"] is True


@pytest.mark.asyncio
async def test_stable_target_double_read_survives_overlay_recreation(monkeypatch):
    leases = [
        {"rebound": True, "selector": "#menu-old", "score": 100, "surface_proof": {"selector": "#menu-old", "id": "actions-42", "opener_control_ids": ["actions-42"]}},
        {"rebound": True, "selector": "#menu-new", "score": 140, "surface_proof": {"selector": "#menu-new", "id": "actions-42", "opener_control_ids": ["actions-42"]}},
    ]

    async def fake_refresh(page, proof):
        return leases.pop(0)

    resolutions = [
        {"resolved": True, "intent": "edit", "selector": "#menu-old .edit", "label": "Edit", "candidate": {"icon": "pencil"}},
        {"resolved": True, "intent": "edit", "selector": "#menu-new .edit", "label": "Edit", "candidate": {"icon": "pencil"}},
    ]

    async def fake_resolve(page, **kwargs):
        result = resolutions.pop(0)
        assert kwargs["within_surface_selector"] in {"#menu-old", "#menu-new"}
        return result

    class Page:
        async def wait_for_timeout(self, ms):
            return None

    monkeypatch.setattr(semantic_module, "_refresh_surface_lease", fake_refresh)
    monkeypatch.setattr(semantic_module, "resolve_semantic_affordance", fake_resolve)

    result = await resolve_affordance_in_proven_surface(
        Page(), intent="edit", surface_selector="#menu-old",
        surface_proof={"selector": "#menu-old", "id": "actions-42", "opener_control_ids": ["actions-42"]},
        max_scrolls=0, stable_reads=2,
    )
    assert result["resolved"] is True
    assert result["selector"] == "#menu-new .edit"
    assert result["surface_provenance"]["selector"] == "#menu-new"
    assert result["surface_provenance"]["stable_reads"] == 2
    assert result["surface_provenance"]["lease_revalidated"] is True
    assert result["virtualized_surface_scrolling"] is False


@pytest.mark.asyncio
async def test_final_membership_rejects_global_duplicate_target(monkeypatch):
    async def fake_refresh(page, proof):
        return {
            "rebound": True,
            "selector": "#proven-menu",
            "score": 140,
            "surface_proof": {"selector": "#proven-menu", "id": "actions-42", "opener_control_ids": ["actions-42"]},
        }

    class Page:
        async def evaluate(self, script, args):
            assert args["surfaceSelector"] == "#proven-menu"
            assert args["targetSelector"] == ".deploy"
            # One Deploy is inside the proven menu, one is a global page action.
            return {"pass": False, "reason": "target_not_unique_inside_surface", "surface_count": 1, "target_count": 2, "inside_count": 1}

    monkeypatch.setattr(semantic_module, "_refresh_surface_lease", fake_refresh)
    result = await verify_affordance_target_membership(
        Page(), target_selector=".deploy",
        surface_proof={"selector": "#old-menu", "id": "actions-42", "opener_control_ids": ["actions-42"]},
    )
    assert result["pass"] is False
    assert result["membership"]["target_count"] == 2
    assert result["membership"]["inside_count"] == 1
