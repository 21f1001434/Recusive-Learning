from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hip_id_agent.aia_client import (
    AIAClient,
    build_chat_payload_variants,
    extract_aia_response_text,
    extract_json_object,
    resolve_output_token_limit,
)
from hip_id_agent.config import AIAConfig, AppConfig, VisionRuntimeConfig
from hip_id_agent.live_runtime_certification import _probe_text_model
from hip_id_agent.vision_runtime import VisionRuntimeBridge


def test_default_output_limit_is_none(monkeypatch: pytest.MonkeyPatch):
    for name in ("AIA_MAX_OUTPUT_TOKENS", "AIA_TEXT_MAX_OUTPUT_TOKENS", "AIA_VISION_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    assert resolve_output_token_limit() is None
    assert resolve_output_token_limit(vision=True) is None


def test_zero_and_unlimited_disable_output_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIA_TEXT_MAX_OUTPUT_TOKENS", "unlimited")
    assert resolve_output_token_limit() is None
    monkeypatch.setenv("AIA_TEXT_MAX_OUTPUT_TOKENS", "0")
    assert resolve_output_token_limit() is None


def test_positive_optional_limit_still_supported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIA_TEXT_MAX_OUTPUT_TOKENS", "4096")
    assert resolve_output_token_limit() == 4096


def test_uncapped_payload_omits_both_limit_fields():
    payloads = build_chat_payload_variants({"model": "x", "messages": []}, output_token_limit=None)
    assert len(payloads) == 1
    assert "max_tokens" not in payloads[0]
    assert "max_completion_tokens" not in payloads[0]


def test_reasoning_does_not_pollute_final_answer():
    data = {
        "choices": [{
            "message": {
                "reasoning_content": "internal analysis that should not be returned",
                "content": '{"pass": true, "reason": "verified"}',
            }
        }]
    }
    text = extract_aia_response_text(data)
    assert text == '{"pass": true, "reason": "verified"}'
    assert "internal analysis" not in text


def test_reasoning_is_fallback_when_final_answer_absent():
    data = {"choices": [{"message": {"content": None, "reasoning_content": "HIP_TEXT_OK"}}]}
    assert extract_aia_response_text(data) == "HIP_TEXT_OK"


def test_json_extraction_handles_fence_and_leading_prose():
    assert extract_json_object('```json\n{"pass": true}\n```')["pass"] is True
    assert extract_json_object('Here is the result: {"pass": true, "confidence": 0.99} trailing')["confidence"] == 0.99


def test_chat_rest_default_request_has_no_output_cap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIA_ENDPOINT", "https://example.invalid/v1/chat/completions")
    monkeypatch.delenv("AIA_TEXT_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("AIA_MAX_OUTPUT_TOKENS", raising=False)
    client = AIAClient(AIAConfig(enabled=True))
    monkeypatch.setattr(client.token_provider, "get_token", lambda: "token")
    captured = []

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def post(*args, **kwargs):
        captured.append(kwargs["json"])
        return Resp()

    monkeypatch.setattr("hip_id_agent.aia_client.requests.post", post)
    assert client.chat_rest([{"role": "user", "content": "hello"}]) == "OK"
    assert len(captured) == 1
    assert "max_tokens" not in captured[0]
    assert "max_completion_tokens" not in captured[0]


def test_chat_rest_optional_cap_uses_compatible_field(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIA_ENDPOINT", "https://example.invalid/v1/chat/completions")
    monkeypatch.setenv("AIA_TEXT_MAX_OUTPUT_TOKENS", "2048")
    client = AIAClient(AIAConfig(enabled=True))
    monkeypatch.setattr(client.token_provider, "get_token", lambda: "token")
    captured = []

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def post(*args, **kwargs):
        captured.append(kwargs["json"])
        return Resp()

    monkeypatch.setattr("hip_id_agent.aia_client.requests.post", post)
    assert client.chat_rest([{"role": "user", "content": "hello"}]) == "OK"
    assert captured[0]["max_completion_tokens"] == 2048


@pytest.mark.asyncio
async def test_live_text_probe_passes_none_to_client(monkeypatch: pytest.MonkeyPatch):
    cfg = AppConfig()
    cfg.aia.enabled = True
    captured = {}
    def fake_chat(self, messages, temperature=0.0, max_tokens=None):
        captured["max_tokens"] = max_tokens
        return "HIP_LIVE_TEXT_OK"
    monkeypatch.setattr(AIAClient, "chat_rest", fake_chat)
    result = await _probe_text_model(cfg)
    assert result["pass"] is True
    assert captured["max_tokens"] is None


def test_vision_preflight_default_request_is_uncapped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-test")
    monkeypatch.setenv("AIA_TOKEN", "token")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("AIA_VISION_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("AIA_MAX_OUTPUT_TOKENS", raising=False)
    bridge = VisionRuntimeBridge(VisionRuntimeConfig(), aia_config=AIAConfig(enabled=True))
    captured = []

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "left red and right blue"}}]}

    def post(*args, **kwargs):
        captured.append(kwargs["json"])
        return Resp()

    monkeypatch.setattr("hip_id_agent.vision_runtime.requests.post", post)
    result = bridge._sync_preflight(force_probe=True)
    assert result["pass"] is True
    assert result["response_preview"]
    assert "max_tokens" not in captured[0]
    assert "max_completion_tokens" not in captured[0]


def test_vision_json_uses_robust_response_extractor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-test")
    monkeypatch.setenv("AIA_TOKEN", "token")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    bridge = VisionRuntimeBridge(VisionRuntimeConfig(), aia_config=AIAConfig(enabled=True))
    bridge._selected_model = "gemma-test"

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {
                "choices": [{
                    "message": {
                        "reasoning_content": "I inspected the image carefully",
                        "content": [{"type": "text", "text": '```json\n{"confidence":0.98,"blocking":false}\n```'}],
                    }
                }]
            }

    monkeypatch.setattr("hip_id_agent.vision_runtime.requests.post", lambda *a, **k: Resp())
    result = bridge._sync_multimodal_json(system="strict json", instruction="inspect", image_bytes=b"png")
    assert result["confidence"] == 0.98
    assert result["blocking"] is False
    assert "inspected the image" not in result["response_preview"]


def test_release_version_is_222():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")
