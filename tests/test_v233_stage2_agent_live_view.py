from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import app
from hip_id_agent.agent_live_view import AgentLiveViewRecorder, read_agent_live_view


class _FakeWebsiteUnderstanding:
    async def capture(self, **kwargs):
        return {
            "active_surface": {
                "label": "Create BizFlow",
                "role": "dialog",
                "tag": "dds-drawer",
                "ariaModal": "true",
            },
            "understanding_gate": {"confidence": 0.98},
            "tabs": [{"text": "Configure Routing", "selected": True}],
            "option_catalog": {
                "process-step": {
                    "label": "Process Step",
                    "section": "Configure Routing",
                    "options": [
                        {"text": "Translation", "visible": True},
                        {"text": "Passthrough", "visible": True},
                        {"text": "Split", "visible": True},
                    ],
                }
            },
        }


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/bizexchange/bizflows"


class _Cfg:
    portal_learning = SimpleNamespace(
        agent_live_view_enabled=True,
        agent_live_view_capture_screenshots=False,
        agent_live_view_capture_website_summary=True,
        agent_live_view_history_limit=50,
    )


def _resolution():
    return {
        "semantic_control_id": "bizflow.routing.process_step",
        "confidence": 0.97,
        "margin": 0.18,
        "status": "resolved",
        "candidate": {
            "label": "Process Step",
            "section": "Configure Routing",
            "role": "combobox",
            "type": "dds-dropdown",
            "selector": '#dds-generated-123',
            "x": 5555,
            "y": 333,
        },
        "evidence": {
            "ranked_candidates": [
                {
                    "semantic_control_id": "bizflow.routing.process_step",
                    "label": "Process Step",
                    "section": "Configure Routing",
                    "role": "combobox",
                    "score": 0.97,
                    "anchored": True,
                    "selector": '#dds-generated-123',
                    "bounding_box": {"x": 123, "y": 44},
                    "reasons": ["active foreground drawer", "section match", "role match"],
                },
                {
                    "semantic_control_id": "background.process_step",
                    "label": "Process Step",
                    "section": "Existing Flow Detail",
                    "role": "combobox",
                    "score": 0.71,
                    "anchored": False,
                    "selector": '#background-1',
                    "reasons": ["background surface"],
                },
            ],
            "vision": {"aligned": True},
        },
    }


def test_stage2_live_view_records_selection_without_selectors_or_coordinates(tmp_path: Path):
    rec = AgentLiveViewRecorder(config=_Cfg(), run_dir=tmp_path)
    rec.website_understanding = _FakeWebsiteUnderstanding()
    asyncio.run(rec.record_selection(
        page=_FakePage(),
        locator=None,
        action_id="a-1",
        phase="biz_flow",
        action="select",
        intent="Select Process Step",
        expected_value="Translation",
        resolution=_resolution(),
    ))
    view = read_agent_live_view(tmp_path)
    cur = view["current"]
    assert cur["selected_control"]["label"] == "Process Step"
    assert cur["current_dropdown_options"] == ["Translation", "Passthrough", "Split"]
    assert cur["candidate_ranking"][0]["selected"] is True
    assert cur["candidate_ranking"][1]["selected"] is False
    assert cur["candidate_ranking"][1]["rejection_reason"]
    rendered = json.dumps(view)
    assert "#dds-generated-123" not in rendered
    assert "#background-1" not in rendered
    assert '"x": 5555' not in rendered
    assert '"bounding_box"' not in rendered


def test_stage2_live_view_records_executor_and_verified_effect(tmp_path: Path):
    rec = AgentLiveViewRecorder(config=_Cfg(), run_dir=tmp_path)
    rec.website_understanding = _FakeWebsiteUnderstanding()
    asyncio.run(rec.record_selection(
        page=_FakePage(), locator=None, action_id="a-2", phase="biz_flow", action="select",
        intent="Select Process Step", expected_value="Translation", resolution=_resolution(),
    ))
    rec.record_planner(action_id="a-2", decision={
        "framework": "AutoWebGLM", "status": "aligned", "aligned": True,
        "reason": "Process Step matches current routing goal",
    })
    event = SimpleNamespace(
        action_id="a-2", success=True, target="Process Step", backend="playwright-mcp",
        page_url_after=_FakePage.url,
        execution_provenance={
            "primary_executor": "pyautogui-mcp",
            "actual_executor": "playwright-mcp",
            "fallback_reason": "desktop coordinate calibration unsafe",
            "playwright_mcp_attempted": True,
            "playwright_mcp_succeeded": True,
            "semantic_revalidation": "same live control re-proven",
            "semantic_effect_pass": True,
            "semantic_effect_type": "dropdown_value_committed",
            "semantic_effect_confidence": 0.99,
            "exact_value_commit_verified": True,
            "observed_value": "Translation",
        },
        error="",
    )
    asyncio.run(rec.record_result(page=_FakePage(), event=event))
    cur = read_agent_live_view(tmp_path)["current"]
    assert cur["planner"]["aligned"] is True
    assert cur["execution"]["actual_executor"] == "playwright-mcp"
    assert cur["execution"]["fallback_reason"] == "desktop coordinate calibration unsafe"
    assert cur["verification"]["status"] == "verified"
    assert cur["verification"]["exact_value_commit_verified"] is True
    assert cur["verification"]["observed_value"] == "Translation"


def test_stage2_backend_live_view_and_screenshot_endpoint(tmp_path: Path, monkeypatch):
    run = tmp_path / "run-123"
    run.mkdir()
    (run / "mission_trace.json").write_text('{"run_id":"run-123","steps":[]}', encoding="utf-8")
    shot = run / "agent_live_view" / "screenshots" / "00001_select.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "agent_live_view.json").write_text(json.dumps({
        "schema_version":"hip.agent-live-view.v1",
        "current":{"screenshot_relative_path":"agent_live_view/screenshots/00001_select.png"},
        "history":[],
    }), encoding="utf-8")
    import backend.app as backend_app
    config_path = str((Path(__file__).resolve().parents[1] / "config.yaml").resolve())
    monkeypatch.setattr(backend_app, "ROOT", tmp_path)
    client = TestClient(app)
    r = client.get("/api/mission/live-view", params={"config": config_path, "runs_dir": str(tmp_path), "run_id": "run-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert "screenshot_url" in body and "path=agent_live_view%2Fscreenshots%2F00001_select.png" in body["screenshot_url"]
    shot_resp = client.get("/api/mission/live-view/screenshot", params={
        "config": config_path, "runs_dir": str(tmp_path), "run_id":"run-123", "path":"agent_live_view/screenshots/00001_select.png"
    })
    assert shot_resp.status_code == 200


def test_stage2_control_center_contains_browser_use_style_live_view():
    html = Path("webui/index.html").read_text(encoding="utf-8")
    js = Path("webui/app.js").read_text(encoding="utf-8")
    css = Path("webui/styles.css").read_text(encoding="utf-8")
    assert "Agent Live View" in html
    for token in ["agentLiveScreenshot", "agentLiveCandidates", "agentLiveOptions", "agentLivePlanner", "agentLiveExecution", "agentLiveVerification"]:
        assert token in html
    assert "/api/mission/live-view" in js
    assert "renderAgentLiveView" in js
    assert "candidate-selected" in css
