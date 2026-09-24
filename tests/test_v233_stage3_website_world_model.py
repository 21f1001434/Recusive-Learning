from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import app
from hip_id_agent.agent_live_view import AgentLiveViewRecorder, read_agent_live_view
from hip_id_agent.config import AppConfig
from hip_id_agent.semantic_control import rank_semantic_candidates
from hip_id_agent.website_world_model import (
    WebsiteWorldModelMemory,
    scrub_world_model_payload,
    semantic_state_descriptor,
)


def _candidate(label="Process Step", section="Configure Routing"):
    return {
        "label": label,
        "section": section,
        "role": "combobox",
        "type": "text",
        "name": "processStep",
        "formControlName": "processStep",
        "required": True,
        "visible": True,
        "enabled": True,
        "inActiveSurface": True,
        "selector": '#dds-form-field-971802467',
        "x": 5638,
        "y": 341,
        "bounding_box": {"x": 100, "y": 200, "width": 300, "height": 40},
        "value": "Translation",
    }


def _state(label="Create BizFlow", extra=None):
    controls = [_candidate()]
    if extra:
        controls.append(extra)
    return {
        "url": "https://developer.dell.com/hybrid-integrations/bizexchange/bizflows?drawer=create",
        "active_surface": {"tag": "dds-drawer", "role": "dialog", "ariaModal": "true", "label": label},
        "tabs": [{"text": "Configure Routing", "selected": True}],
        "controls": controls,
    }


def test_stage3_scrubber_never_persists_selectors_coordinates_or_values():
    raw = {
        "selector": "#dds-form-field-123456",
        "xpath": "//input[1]",
        "x": 5638,
        "bounding_box": {"x": 10, "y": 20},
        "expected_value": "Translation",
        "payload": {"secret": "abc"},
        "label": "Process Step",
        "section": "Configure Routing",
        "nested": {"css_selector": ".dds__input", "role": "combobox"},
    }
    safe = scrub_world_model_payload(raw)
    blob = json.dumps(safe)
    for forbidden in ["selector", "xpath", "bounding_box", "5638", "Translation", "abc", "dds-form-field-123456"]:
        assert forbidden not in blob
    assert safe["label"] == "Process Step"
    assert safe["nested"]["role"] == "combobox"


def test_stage3_verified_transition_promotes_and_failure_demotes(tmp_path: Path):
    cfg = AppConfig().brain
    mem = WebsiteWorldModelMemory(tmp_path / "world", config=cfg)
    before = _state()
    after = _state(extra={
        "label": "Map Name", "section": "Configure Routing", "role": "textbox", "type": "text",
        "visible": True, "enabled": True, "inActiveSurface": True,
    })
    effect = {"pass": True, "effect_type": "owned_surface_changed", "confidence": 0.99}
    promoted = mem.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing",
        candidate=_candidate(), before=before, after=after, effect=effect,
    )
    assert promoted["status"] == "promoted"
    hint = mem.control_hint(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing", candidate=_candidate()
    )
    assert hint["matched"] is True
    assert hint["trust"] == "validated"
    assert hint["score"] > 0
    recommendations = mem.recommend_actions(phase="biz_flow", current_state=before)
    assert recommendations and recommendations[0]["requires_live_reproof"] is True

    # Enough failures must reduce trust/score instead of reinforcing stale knowledge.
    for _ in range(4):
        mem.record_failure(
            phase="biz_flow", action="select", label="Process Step", section="Configure Routing",
            candidate=_candidate(), before=before, after=before,
            effect={"pass": False, "effect_type": "no_proven_semantic_effect", "confidence": 0.0},
        )
    hint2 = mem.control_hint(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing", candidate=_candidate()
    )
    assert hint2["failure_count"] >= 4
    assert hint2["score"] < hint["score"]


def test_stage3_world_model_is_bounded_prior_in_candidate_ranking(tmp_path: Path):
    mem = WebsiteWorldModelMemory(tmp_path / "world", config=AppConfig().brain)
    good = _candidate()
    before = _state()
    after = _state(extra={"label": "Child Field", "section": "Configure Routing", "role": "textbox", "visible": True, "enabled": True, "inActiveSurface": True})
    mem.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing",
        candidate=good, before=before, after=after,
        effect={"pass": True, "effect_type": "child_control_revealed", "confidence": 0.98},
    )
    background = {**_candidate(), "section": "Existing Flow Detail", "inActiveSurface": False, "label": "Process Step"}
    ranked = rank_semantic_candidates(
        candidates=[background, good], action="select", expected_label="Process Step",
        expected_section="Configure Routing", world_model=mem, phase="biz_flow",
    )
    assert ranked[0]["candidate"]["section"] == "Configure Routing"
    assert ranked[0]["world_model"]["trust"] == "validated"
    assert "validated_world_model_memory" in ranked[0]["reasons"]


def test_stage3_state_descriptor_is_value_free_and_generation_independent():
    state = _state()
    desc = semantic_state_descriptor(state, phase="biz_flow")
    blob = json.dumps(desc)
    assert "Translation" not in blob
    assert "5638" not in blob
    assert "dds-form-field" not in blob
    assert desc["active_surface"]["label"] == "Create BizFlow"
    assert desc["semantic_state_id"]


class _Cfg:
    portal_learning = SimpleNamespace(
        agent_live_view_enabled=True,
        agent_live_view_capture_screenshots=False,
        agent_live_view_capture_website_summary=False,
        agent_live_view_history_limit=50,
    )
    reporting = SimpleNamespace(memory_dir="")
    brain = AppConfig().brain


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/bizexchange/bizflows"


def test_stage3_live_view_exposes_memory_without_brittle_replay(tmp_path: Path):
    cfg = _Cfg()
    cfg.reporting = SimpleNamespace(memory_dir=str(tmp_path / "memory"))
    rec = AgentLiveViewRecorder(config=cfg, run_dir=tmp_path / "run")
    rec.world_model.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing",
        candidate=_candidate(), before=_state(), after=_state(extra={"label": "Child", "section": "Configure Routing", "role": "textbox", "visible": True, "enabled": True, "inActiveSurface": True}),
        effect={"pass": True, "effect_type": "child_revealed", "confidence": 0.99},
    )
    resolution = {
        "semantic_control_id": "bizflow.routing.process_step",
        "confidence": 0.97,
        "margin": 0.2,
        "status": "resolved",
        "candidate": _candidate(),
        "evidence": {"ranked_candidates": []},
    }
    asyncio.run(rec.record_selection(
        page=_FakePage(), locator=None, action_id="a3", phase="biz_flow", action="select",
        intent="Select Process Step", expected_value="Translation", resolution=resolution,
    ))
    cur = read_agent_live_view(tmp_path / "run")["current"]
    assert cur["website_memory"]["summary"]["transitions"]["validated"] >= 1
    assert cur["website_memory"]["live_reproof_required"] is True
    blob = json.dumps(cur["website_memory"])
    assert "#dds-form-field" not in blob
    assert "5638" not in blob


def test_stage3_config_and_control_center_enable_world_model():
    cfg = AppConfig()
    assert cfg.brain.world_model_enabled is True
    assert cfg.brain.world_model_use_for_candidate_scoring is True
    assert cfg.brain.world_model_use_for_planning is True
    html = Path("webui/index.html").read_text(encoding="utf-8")
    js = Path("webui/app.js").read_text(encoding="utf-8")
    assert "Persistent semantic website memory" in html
    assert "agentLiveMemory" in html and "agentLiveMemoryHints" in html
    assert "website_memory" in js
    assert "/api/mission/world-model" in Path("backend/app.py").read_text(encoding="utf-8")


def test_stage3_backend_world_model_endpoint(tmp_path: Path, monkeypatch):
    import backend.app as backend_app
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(Path("config.yaml").read_text(encoding="utf-8").replace('./data/hip_memory', str(tmp_path / 'memory')), encoding="utf-8")
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    client = TestClient(app)
    r = client.get("/api/mission/world-model", params={"config": str(cfg_path), "phase": "biz_flow"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["summary"]["safety"]["customer_values_stored"] is False


def test_stage3_browser_session_feeds_world_memory_to_autowebglm_source_contract():
    src = Path("hip_id_agent/browser_session.py").read_text(encoding="utf-8")
    assert "world_model.planner_hints" in src or "wm.planner_hints" in src
    assert "world_model_hints" in src
    assert "live_reproof_required" in src
    recovery = Path("hip_id_agent/runtime_self_heal.py").read_text(encoding="utf-8")
    assert "website_world_model_recommendations" in recovery


def test_stage3_portal_choice_memory_accepts_only_mounted_portal_options(tmp_path: Path):
    mem = WebsiteWorldModelMemory(tmp_path / "world", config=AppConfig().brain)
    control = {
        "label": "Process Step", "section": "Configure Routing", "role": "combobox",
        "visible": True, "enabled": True, "inActiveSurface": True,
        "selector": "#transient-dds-id", "value": "should-not-persist",
    }
    ok = mem.record_verified_portal_choice(
        phase="biz_flow", action="select", control=control, choice="Translation",
        available_options=["Translation", "Passthrough", "Split"],
        effect_type="selection_committed", effect_confidence=0.99,
    )
    assert ok["status"] == "promoted"
    hints = mem.portal_choice_hints(phase="biz_flow", label="Process Step", section="Configure Routing")
    assert hints and hints[0]["portal_choice"] == "Translation"
    blob = json.dumps(hints)
    assert "#transient-dds-id" not in blob
    assert "should-not-persist" not in blob

    rejected = mem.record_verified_portal_choice(
        phase="biz_flow", action="fill", control=control, choice="CUSTOMER-12345",
        available_options=["Translation", "Passthrough", "Split"],
        effect_type="text_committed", effect_confidence=0.99,
    )
    assert rejected["status"] == "not_portal_metadata"
    assert "CUSTOMER-12345" not in json.dumps(mem.summary(phase="biz_flow"))


def test_stage3_transition_learns_revealed_dependency_without_values(tmp_path: Path):
    mem = WebsiteWorldModelMemory(tmp_path / "world", config=AppConfig().brain)
    before = _state()
    child = {
        "label": "Expression", "section": "Configure Routing", "role": "textbox",
        "type": "text", "formControlName": "expression", "required": True,
        "visible": True, "enabled": True, "inActiveSurface": True,
        "value": "customer-sensitive-expression",
        "selector": "#generated-child-123",
    }
    after = _state(extra=child)
    promoted = mem.record_verified_transition(
        phase="biz_flow", action="select", label="Process Step", section="Configure Routing",
        candidate=_candidate(), before=before, after=after,
        effect={"pass": True, "effect_type": "child_control_revealed", "confidence": 0.99},
    )
    revealed = promoted["transition"]["revealed_controls"]
    assert any(str(x.get("label") or "").lower() == "expression" for x in revealed)
    blob = json.dumps(promoted)
    assert "customer-sensitive-expression" not in blob
    assert "#generated-child-123" not in blob


def test_stage3_sftp_haft_role_aware_deployment_group_policy():
    from hip_id_agent.deployment_group_policy import (
        SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP,
        SFTP_HAFT_SENDER_DEPLOYMENT_GROUP,
        resolve_transport_deployment_group,
    )
    assert resolve_transport_deployment_group(
        interface_type="SFTP HAFT", profile_usage="Sender", current_value="dce-shared-sender"
    ) == SFTP_HAFT_SENDER_DEPLOYMENT_GROUP
    assert resolve_transport_deployment_group(
        interface_type="SFTP-HAFT", profile_usage="Partner / Receiver", current_value="dce-default-receiver"
    ) == SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP
    # Explicit live/customer overrides remain authoritative.
    assert resolve_transport_deployment_group(
        interface_type="SFTP HAFT", profile_usage="Sender", current_value="custom-sender-dg"
    ) == "custom-sender-dg"
    # The SFTP-HAFT policy must not leak into other interface families.
    assert resolve_transport_deployment_group(
        interface_type="HTTPS AS2", profile_usage="Sender", current_value="as2-dg"
    ) == "as2-dg"


def test_stage3_transport_state_graph_and_examples_use_role_aware_groups():
    from hip_id_agent.deployment_group_policy import (
        SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP,
        SFTP_HAFT_SENDER_DEPLOYMENT_GROUP,
    )
    from hip_id_agent.stateful_form_runtime import compile_transport_profile_state_graph

    payload = {
        "objects": {
            "source_transport_profile": {
                "system_type": "Dell Application", "partner_name": "AIC - DCE", "profile_name": "src",
                "profile_usage": "Sender", "deployment_group": "dce-shared-sender",
                "interface_type": "SFTP HAFT",
            },
            "target_transport_profile": {
                "system_type": "Partner", "partner_name": "U-HAUL", "profile_name": "tgt",
                "profile_usage": "Receiver", "deployment_group": "dce-shared-receiver",
                "interface_type": "SFTP HAFT",
            },
        }
    }
    src = compile_transport_profile_state_graph(payload, "source_transport_profile")
    tgt = compile_transport_profile_state_graph(payload, "target_transport_profile")
    src_node = next(x for x in src["nodes"] if x["field_key"] == "deployment_group")
    tgt_node = next(x for x in tgt["nodes"] if x["field_key"] == "deployment_group")
    assert src_node["expected_value"] == SFTP_HAFT_SENDER_DEPLOYMENT_GROUP
    assert tgt_node["expected_value"] == SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP

    full = json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    assert full["objects"]["source_transport_profile"]["deployment_group"] == SFTP_HAFT_SENDER_DEPLOYMENT_GROUP
    assert full["objects"]["target_transport_profile"]["deployment_group"] == SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP
