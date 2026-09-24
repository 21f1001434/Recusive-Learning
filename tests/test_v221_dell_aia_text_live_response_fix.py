from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hip_id_agent.aia_client import AIAClient, extract_aia_response_text
from hip_id_agent.config import AppConfig, AIAConfig
from hip_id_agent.live_runtime_certification import _probe_text_model


def test_choice_level_reasoning_content_is_extracted():
    data = {"choices": [{"index": 0, "reasoning_content": "HIP_TEXT_OK", "finish_reason": "stop"}]}
    assert extract_aia_response_text(data) == "HIP_TEXT_OK"


def test_choice_level_output_text_is_extracted():
    data = {"choices": [{"output_text": "HIP_TEXT_OK", "finish_reason": "stop"}]}
    assert extract_aia_response_text(data) == "HIP_TEXT_OK"


def test_message_reasoning_alias_is_extracted():
    data = {"choices": [{"message": {"role": "assistant", "content": None, "reasoning": "HIP_TEXT_OK"}}]}
    assert extract_aia_response_text(data) == "HIP_TEXT_OK"


def test_message_answer_alias_is_extracted():
    data = {"choices": [{"message": {"answer": "HIP_TEXT_OK"}}]}
    assert extract_aia_response_text(data) == "HIP_TEXT_OK"


def test_finish_reason_is_not_mistaken_for_assistant_text():
    data = {"choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": None}}]}
    assert extract_aia_response_text(data) == ""


@pytest.mark.asyncio
async def test_live_text_probe_default_is_uncapped(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    cfg = AppConfig()
    cfg.aia.enabled = True
    monkeypatch.delenv("AIA_TEXT_PROBE_MAX_TOKENS", raising=False)

    def fake_chat(self, messages, temperature=0.0, max_tokens=0):
        captured["max_tokens"] = max_tokens
        return "HIP_LIVE_TEXT_OK"

    monkeypatch.setattr(AIAClient, "chat_rest", fake_chat)
    result = await _probe_text_model(cfg)
    assert result["pass"] is True
    assert captured["max_tokens"] is None


@pytest.mark.asyncio
async def test_legacy_probe_budget_does_not_reintroduce_cap(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    cfg = AppConfig()
    cfg.aia.enabled = True
    monkeypatch.setenv("AIA_TEXT_PROBE_MAX_TOKENS", "16")

    def fake_chat(self, messages, temperature=0.0, max_tokens=0):
        captured["max_tokens"] = max_tokens
        return "HIP_LIVE_TEXT_OK"

    monkeypatch.setattr(AIAClient, "chat_rest", fake_chat)
    result = await _probe_text_model(cfg)
    assert result["pass"] is True
    assert captured["max_tokens"] is None


def test_chat_rest_empty_success_reports_choice_diagnostics(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIA_ENDPOINT", "https://example.invalid/v1/chat/completions")
    cfg = AIAConfig(enabled=True, model="gpt-oss-120b")
    client = AIAClient(cfg)
    monkeypatch.setattr(client.token_provider, "get_token", lambda: "token")

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {
                "choices": [{
                    "index": 0,
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": None},
                }],
                "model": "gpt-oss-120b",
            }

    monkeypatch.setattr("hip_id_agent.aia_client.requests.post", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError) as exc:
        client.chat_rest([{"role": "user", "content": "hello"}], max_tokens=32)
    text = str(exc.value)
    assert "finish_reason=length" in text
    assert "choice_keys=" in text
    assert "message_keys=" in text


def test_backend_text_probe_is_uncapped_by_default(monkeypatch: pytest.MonkeyPatch):
    import backend.app as backend_app
    from fastapi.testclient import TestClient

    cfg = AppConfig()
    cfg.aia.enabled = True
    captured = {}
    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": cfg)
    monkeypatch.setenv("AIA_TEXT_PROBE_MAX_TOKENS", "384")

    def fake_chat(self, messages, temperature=0.0, max_tokens=0):
        captured["max_tokens"] = max_tokens
        return "HIP_TEXT_OK"

    monkeypatch.setattr(AIAClient, "chat_rest", fake_chat)
    response = TestClient(backend_app.app).post("/api/models/text/test", json={"config": "config.yaml"})
    assert response.status_code == 200
    assert response.json()["pass"] is True
    assert captured["max_tokens"] is None


def test_release_version_is_222():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
