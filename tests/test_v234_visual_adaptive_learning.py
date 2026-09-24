from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import PortalBrainConfig, PortalLearningConfig
from hip_id_agent.visual_intelligence_overlay import (
    OVERLAY_COLORS,
    VisualIntelligenceOverlay,
    overlay_legend,
    public_overlay_telemetry,
)
from hip_id_agent.website_world_model import WebsiteWorldModelMemory


class _FirstLocator:
    async def bounding_box(self, timeout=0):
        return {"x": 12, "y": 24, "width": 180, "height": 36}


class _Locator:
    @property
    def first(self):
        return _FirstLocator()


class _Page:
    def __init__(self):
        self.last_script = ""
        self.last_payload = None

    async def evaluate(self, script, payload=None):
        self.last_script = script
        self.last_payload = payload
        if payload is None:
            return None
        return {
            "stage": payload.get("stage"),
            "counts": {
                "seen": 5,
                "candidate": 2,
                "selected": 1 if payload.get("stage") == "selected" else 0,
                "acting": 1 if payload.get("stage") == "acting" else 0,
                "verified": 1 if payload.get("stage") == "verified" else 0,
                "revealed": 1 if payload.get("previousSeenKeys") else 0,
                "failed": 1 if payload.get("stage") == "failed" else 0,
                "foreground": 1,
            },
            "seenKeys": ["button|add|form", "combobox|process step|form"],
            "pointerEvents": "none",
            "foregroundOwnedOnly": True,
        }


def _brain(**kwargs):
    values = {
        "world_model_enabled": True,
        "world_model_min_validated_confirmations": 1,
        "world_model_min_validated_confidence": 0.50,
        "world_model_failure_decay": 0.18,
        "world_model_use_for_candidate_scoring": True,
        "world_model_use_for_planning": True,
        "world_model_recency_decay_enabled": True,
        "world_model_confidence_half_life_days": 30.0,
        "world_model_stale_after_days": 60.0,
        "world_model_drift_failure_threshold": 2,
        "world_model_drift_penalty": 0.22,
        "world_model_auto_demote_on_drift": True,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _state(extra=False):
    controls = [
        {"label": "Process Step", "role": "combobox", "type": "select", "section": "Routing", "visible": True, "inActiveSurface": True},
    ]
    if extra:
        controls.append({"label": "Receiver Transport Profile", "role": "combobox", "type": "select", "section": "Routing", "visible": True, "inActiveSurface": True})
    return {
        "url": "https://hip.example/bizflow",
        "active_surface": {"label": "BizFlow drawer", "role": "dialog"},
        "tabs": [{"text": "Configure Routing", "selected": True}],
        "controls": controls,
    }


def _candidate():
    return {
        "label": "Process Step",
        "section": "Routing",
        "role": "combobox",
        "type": "select",
        "inActiveSurface": True,
    }


def test_v234_overlay_legend_has_all_eight_states():
    assert set(OVERLAY_COLORS) == {"seen", "candidate", "selected", "acting", "verified", "revealed", "failed", "foreground"}
    assert overlay_legend()["seen"]["color"] == "#2f80ed"
    assert overlay_legend()["foreground"]["color"] == "#ff5ca8"


def test_v234_public_overlay_never_persists_runtime_seen_keys_or_geometry():
    public = public_overlay_telemetry({
        "stage": "selected",
        "counts": {"seen": 2},
        "_seen_keys": ["runtime-only"],
        "seenKeys": ["runtime-only-2"],
        "selector": "#never-store",
        "bounding_box": {"x": 1, "y": 2},
    })
    assert "_seen_keys" not in public and "seenKeys" not in public
    assert "selector" not in public and "bounding_box" not in public
    assert public["selectors_stored"] is False
    assert public["coordinates_stored"] is False


@pytest.mark.asyncio
async def test_v234_overlay_renders_selected_acting_and_verified_transiently():
    cfg = SimpleNamespace(portal_learning=SimpleNamespace(
        visual_overlay_enabled=True,
        visual_overlay_max_seen_controls=80,
        visual_overlay_max_candidates=8,
        visual_overlay_show_labels=True,
        visual_overlay_result_hold_ms=1400,
        visual_overlay_highlight_foreground_surface=True,
    ))
    overlay = VisualIntelligenceOverlay(config=cfg)
    page = _Page()
    resolution = {"evidence": {"ranked_candidates": [
        {"selector": "button.add", "label": "Add", "anchored": True},
        {"selector": "[role=combobox]", "label": "Process Step", "anchored": False},
    ]}}
    selected = await overlay.render_selection(page=page, locator=_Locator(), resolution=resolution, label="Add")
    assert selected["stage"] == "selected"
    assert selected["counts"]["seen"] == 5
    assert selected["foreground_owned_only"] is True
    assert selected["_seen_keys"]
    assert "pointerEvents:'none'" in page.last_script
    assert "elementFromPoint" in page.last_script
    assert "aria-modal" in page.last_script

    acting = await overlay.render_acting(page=page, locator=_Locator(), label="Add")
    assert acting["stage"] == "acting" and acting["counts"]["acting"] == 1

    verified = await overlay.render_result(
        page=page, locator=_Locator(), label="Add", success=True,
        previous_seen_keys=selected["_seen_keys"],
    )
    assert verified["stage"] == "verified"
    assert verified["counts"]["verified"] == 1
    assert verified["counts"]["revealed"] == 1


def test_v234_config_defaults_expose_visual_and_adaptive_controls():
    brain = PortalBrainConfig()
    learning = PortalLearningConfig()
    assert brain.world_model_recency_decay_enabled is True
    assert brain.world_model_confidence_half_life_days == 45.0
    assert brain.world_model_drift_failure_threshold == 2
    assert learning.visual_overlay_enabled is True
    assert learning.visual_overlay_max_candidates == 8
    assert learning.visual_overlay_highlight_foreground_surface is True


def test_v234_world_model_confidence_decays_and_old_memory_becomes_stale(tmp_path: Path):
    wm = WebsiteWorldModelMemory(tmp_path / "wm", config=_brain())
    wm.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(True),
        effect={"pass": True, "confidence": 0.98, "effect_type": "dependent_control_revealed"},
    )
    controls_path = tmp_path / "wm" / "controls.json"
    data = json.loads(controls_path.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    for row in data["controls"].values():
        row["last_seen_at"] = old
    controls_path.write_text(json.dumps(data), encoding="utf-8")

    hint = wm.control_hint(phase="biz_flow", action="select", label="Process Step", section="Routing", candidate=_candidate())
    assert hint["matched"] is True
    assert hint["stale"] is True
    assert hint["effective_confidence"] < hint["raw_confidence"]
    assert hint["trust"] == "candidate"


def test_v234_repeated_contradictions_demote_drift_and_success_recovers(tmp_path: Path):
    wm = WebsiteWorldModelMemory(tmp_path / "wm", config=_brain(world_model_min_validated_confidence=0.45))
    good = {"pass": True, "confidence": 0.99, "effect_type": "dependent_control_revealed"}
    bad = {"pass": False, "confidence": 0.95, "effect_type": "no_proven_effect"}

    # Establish verified semantics first.
    wm.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(True), effect=good,
    )
    wm.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(True), effect=good,
    )
    # Contradictions attach negative evidence to the same semantic control.
    wm.record_failure(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(False), effect=bad,
    )
    wm.record_failure(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(False), effect=bad,
    )
    summary = wm.summary(phase="biz_flow")
    assert summary["learning_health"]["drift_suspect_count"] >= 1
    hint = wm.control_hint(phase="biz_flow", action="select", label="Process Step", section="Routing", candidate=_candidate())
    assert hint["drift_suspect"] is True
    assert hint["trust"] == "candidate"

    # Live success is authoritative and re-stabilizes the semantic control.
    wm.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Routing",
        candidate=_candidate(), before=_state(False), after=_state(True), effect=good,
    )
    recovered = wm.control_hint(phase="biz_flow", action="select", label="Process Step", section="Routing", candidate=_candidate())
    assert recovered["drift_suspect"] is False
    assert recovered["consecutive_failure_count"] == 0


def test_v234_planner_uses_effective_confidence_not_stale_raw_confidence(tmp_path: Path):
    wm = WebsiteWorldModelMemory(tmp_path / "wm", config=_brain())
    wm.record_verified_transition(
        phase="transport_profile", action="click", label="Add", section="Header",
        candidate={"label":"Add","section":"Header","role":"button","type":"button","inActiveSurface":True},
        before=_state(False), after=_state(True),
        effect={"pass": True, "confidence": 0.99, "effect_type": "drawer_opened"},
    )
    path=tmp_path / "wm" / "transitions.json"
    data=json.loads(path.read_text())
    old=(datetime.now(timezone.utc)-timedelta(days=180)).isoformat()
    for row in data["transitions"].values(): row["last_seen_at"]=old
    path.write_text(json.dumps(data),encoding="utf-8")
    hints=wm.planner_hints(phase="transport_profile", action="click", label="Add", section="Header")
    assert hints
    assert hints[0]["confidence"] < hints[0]["raw_confidence"]
    assert hints[0]["stale"] is True
    assert hints[0]["trust"] == "candidate"


def test_v234_control_center_contains_visual_and_adaptive_panels():
    html=Path("webui/index.html").read_text(encoding="utf-8")
    js=Path("webui/app.js").read_text(encoding="utf-8")
    css=Path("webui/styles.css").read_text(encoding="utf-8")
    for token in ["agentVisualStateBanner","agentOverlayCounters","agentLearningHealth","agentDriftKnowledge","agentDecisionHistory","adaptiveMetric"]:
        assert token in html
    for color in ["#2f80ed","#9b51e0","#f2c94c","#f2994a","#27ae60","#18b7b0","#eb5757","#ff5ca8"]:
        assert color in html
    assert "/api/mission/world-model" in js
    assert "renderAdaptiveMetric" in js
    assert "visual-counter-grid" in css
    assert "decision-timeline" in css


def test_v234_browser_session_has_three_pre_dispatch_acting_updates():
    src=Path("hip_id_agent/browser_session.py").read_text(encoding="utf-8")
    assert src.count("record_acting(page=self.page, action_id=ev.action_id)") == 3


def test_v234_release_version():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
