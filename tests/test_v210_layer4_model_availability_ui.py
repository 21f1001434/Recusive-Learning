from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import backend.app as backend_app
from hip_id_agent.vision_runtime import VisionRuntimeBridge

ROOT = Path(__file__).resolve().parents[1]


def _cfg():
    return SimpleNamespace(aia=SimpleNamespace(), vision_runtime=SimpleNamespace())


def test_text_model_live_probe_success(monkeypatch):
    class FakeAIA:
        def __init__(self, config):
            self.config = config
        def provider_summary(self):
            return {
                "llm_provider": "Dell AIA GenAI Gateway",
                "model": "gpt-oss-120b",
                "endpoint_configured": True,
                "auth_mode": "sso",
                "provider_lock": "DELL_AIA_ONLY",
            }
        def chat_rest(self, messages, temperature=0.0, max_tokens=None):
            assert messages[-1]["content"] == "Return exactly HIP_TEXT_OK and nothing else."
            assert max_tokens is None
            return "HIP_TEXT_OK"

    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": _cfg())
    monkeypatch.setattr(backend_app, "AIAClient", FakeAIA)
    response = TestClient(backend_app.app).post("/api/models/text/test", json={"config": "config.yaml"})
    assert response.status_code == 200
    data = response.json()
    assert data["pass"] is True
    assert data["status"] == "available"
    assert data["model"] == "gpt-oss-120b"
    assert data["expected_marker_seen"] is True
    assert data["endpoint_configured"] is True
    assert "endpoint" not in data  # never return the configured URL/token to the browser UI


def test_text_model_probe_classifies_auth_failure(monkeypatch):
    class FakeAIA:
        def __init__(self, config): pass
        def provider_summary(self):
            return {"llm_provider":"Dell AIA GenAI Gateway", "model":"gpt-oss-120b", "endpoint_configured":True, "auth_mode":"sso"}
        def chat_rest(self, *args, **kwargs):
            raise RuntimeError("HTTP 401: unauthorized token")

    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": _cfg())
    monkeypatch.setattr(backend_app, "AIAClient", FakeAIA)
    data = TestClient(backend_app.app).post("/api/models/text/test", json={}).json()
    assert data["pass"] is False
    assert data["error_kind"] == "authentication"
    assert data["response_received"] is False


def test_vision_model_button_forces_fresh_image_understanding_probe(monkeypatch):
    calls = []
    class FakeVision:
        def __init__(self, config, *, aia_config): pass
        async def preflight(self, *, force=False):
            calls.append(force)
            return {"pass": True, "status":"ok", "model":"gemma-3-27b-it", "attempts":[{"model":"gemma-3-27b-it","pass":True,"http_status":200}]}
        def status(self): return {"selected_model":"gemma-3-27b-it"}
        def _endpoint(self): return "https://configured.invalid/chat/completions"

    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": _cfg())
    monkeypatch.setattr(backend_app, "VisionRuntimeBridge", FakeVision)
    response = TestClient(backend_app.app).post("/api/models/vision/test", json={"config":"config.yaml"})
    assert response.status_code == 200
    data = response.json()
    assert calls == [True]
    assert data["pass"] is True
    assert data["image_understanding_verified"] is True
    assert data["model"] == "gemma-3-27b-it"
    assert data["http_statuses"] == [200]
    assert "endpoint" not in data


def test_vision_runtime_force_preflight_bypasses_cached_success(monkeypatch):
    bridge = object.__new__(VisionRuntimeBridge)
    import asyncio
    bridge._preflight = {"pass": True, "status":"ok", "model":"old"}
    bridge._preflight_lock = asyncio.Lock()
    seen = []
    def fresh(*, force_probe=False):
        seen.append(force_probe)
        return {"pass": True, "status":"ok", "model":"fresh"}
    bridge._sync_preflight = fresh
    result = asyncio.run(bridge.preflight(force=True))
    assert seen == [True]
    assert result["model"] == "fresh"


def test_javascript_ui_ships_text_and_vision_model_test_controls_and_completion_defaults():
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
    assert 'id="testTextModelBtn"' in html
    assert 'id="testVisionModelBtn"' in html
    assert "Test text model" in html and "Test vision model" in html
    assert "/api/models/${kind}/test" in js
    assert 'testModelAvailability("text")' in js
    assert 'testModelAvailability("vision")' in js
    # Heavy evidence is opt-in for an operational completion-first run; it must not
    # silently recreate the long-path/evidence workload seen in the live failure.
    assert 'id="heavyEvidence" type="checkbox" checked' not in html
