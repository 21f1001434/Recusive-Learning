from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import pytest

from hip_id_agent.aia_client import AIAClient, extract_aia_response_text
from hip_id_agent.config import AIAConfig, AppConfig, VisionRuntimeConfig
from hip_id_agent.live_runtime_certification import _probe_pyautogui_mcp_readonly, _probe_text_model
from hip_id_agent.mcp_stdio import MCPClientError, MCPStdioClient
from hip_id_agent.runtime_env import load_runtime_env
from hip_id_agent.vision_runtime import VisionRuntimeBridge


def test_extract_aia_text_from_string_content():
    assert extract_aia_response_text({"choices": [{"message": {"content": "HIP_TEXT_OK"}}]}) == "HIP_TEXT_OK"


def test_extract_aia_text_from_content_parts():
    data = {"choices": [{"message": {"content": [{"type": "text", "text": "HIP_TEXT_OK"}]}}]}
    assert "HIP_TEXT_OK" in extract_aia_response_text(data)


def test_extract_aia_text_from_reasoning_content():
    data = {"choices": [{"message": {"content": "", "reasoning_content": "HIP_TEXT_OK"}}]}
    assert "HIP_TEXT_OK" in extract_aia_response_text(data)


def test_extract_aia_text_from_responses_style_output():
    data = {"output": [{"content": [{"type": "output_text", "text": "HIP_TEXT_OK"}]}]}
    assert "HIP_TEXT_OK" in extract_aia_response_text(data)


def test_extract_aia_text_deduplicates_mirrored_content():
    data = {"choices": [{"message": {"content": "OK", "reasoning_content": "OK"}}]}
    assert extract_aia_response_text(data) == "OK"


def test_runtime_env_loads_project_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VISION_MODEL_NAME", raising=False)
    monkeypatch.delenv("HIP_ENV_FILE", raising=False)
    (tmp_path / ".env").write_text("VISION_MODEL_NAME=gemma-test-vision\n", encoding="utf-8")
    result = load_runtime_env(tmp_path)
    assert result["loaded"] is True
    assert os.environ.get("VISION_MODEL_NAME") == "gemma-test-vision"


def test_runtime_env_explicit_file_has_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    explicit = tmp_path / "external.env"
    explicit.write_text("VISION_MODEL_NAME=gemma-explicit\n", encoding="utf-8")
    monkeypatch.setenv("HIP_ENV_FILE", str(explicit))
    monkeypatch.delenv("VISION_MODEL_NAME", raising=False)
    result = load_runtime_env(tmp_path)
    assert result["loaded_files"][0] == str(explicit)
    assert os.environ.get("VISION_MODEL_NAME") == "gemma-explicit"


def test_mcp_stdio_default_stream_limit_is_large_enough_for_screenshots():
    client = MCPStdioClient(command="dummy")
    assert client.stream_limit_bytes >= 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_mcp_stdio_passes_stream_limit_to_subprocess(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    async def fake_create(*args, **kwargs):
        captured.update(kwargs)
        raise FileNotFoundError("dummy")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    client = MCPStdioClient(command="dummy", stream_limit_bytes=5 * 1024 * 1024)
    with pytest.raises(MCPClientError):
        await client.start()
    assert captured["limit"] == 5 * 1024 * 1024


def test_vision_probe_image_is_not_two_pixels():
    payload = VisionRuntimeBridge._probe_payload("gemma-test")
    image_url = payload["messages"][0]["content"][1]["image_url"]["url"]
    raw = base64.b64decode(image_url.split(",", 1)[1])
    # PNG IHDR width/height are bytes 16..24, big-endian.
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    assert (width, height) == (64, 32)


def test_vision_model_alias_is_honored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-3-27b-it")
    bridge = VisionRuntimeBridge(VisionRuntimeConfig(), aia_config=AIAConfig(enabled=True))
    assert bridge._candidates()[0] == "gemma-3-27b-it"


def test_vision_preflight_parses_content_parts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-test")
    monkeypatch.setenv("AIA_TOKEN", "dummy-token")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    bridge = VisionRuntimeBridge(VisionRuntimeConfig(), aia_config=AIAConfig(enabled=True))

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": [{"type": "text", "text": "left red, right blue"}]}}]}

    monkeypatch.setattr("hip_id_agent.vision_runtime.requests.post", lambda *a, **k: Resp())
    result = bridge._sync_preflight(force_probe=True)
    assert result["pass"] is True
    assert result["model"] == "gemma-test"


def test_vision_preflight_retries_alternate_completion_field_on_400(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-test")
    monkeypatch.setenv("AIA_VISION_MAX_OUTPUT_TOKENS", "96")
    monkeypatch.setenv("AIA_TOKEN", "dummy-token")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    bridge = VisionRuntimeBridge(VisionRuntimeConfig(), aia_config=AIAConfig(enabled=True))
    payloads = []

    class Resp400:
        status_code = 400
        text = "bad max_tokens"
        def json(self):
            return {}

    class Resp200:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "red left blue right"}}]}

    def post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return Resp400() if len(payloads) == 1 else Resp200()

    monkeypatch.setattr("hip_id_agent.vision_runtime.requests.post", post)
    result = bridge._sync_preflight(force_probe=True)
    assert result["pass"] is True
    assert "max_completion_tokens" in payloads[0]
    assert "max_tokens" in payloads[1]


@pytest.mark.asyncio
async def test_text_live_probe_accepts_nonempty_success_even_without_exact_marker(monkeypatch: pytest.MonkeyPatch):
    cfg = AppConfig()
    cfg.aia.enabled = True
    monkeypatch.setattr(AIAClient, "chat_rest", lambda self, *a, **k: "Model is alive and responding")
    result = await _probe_text_model(cfg)
    assert result["pass"] is True
    assert result["capability_verified"] is True
    assert result["expected_marker_seen"] is False
    assert result["warning"]


@pytest.mark.asyncio
async def test_text_live_probe_rejects_empty_response(monkeypatch: pytest.MonkeyPatch):
    cfg = AppConfig()
    cfg.aia.enabled = True
    monkeypatch.setattr(AIAClient, "chat_rest", lambda self, *a, **k: "")
    result = await _probe_text_model(cfg)
    assert result["pass"] is False


class _FakePyAutoGUI:
    def __init__(self, *, fail_screenshot: bool = False):
        self.region = None
        self.fail_screenshot = fail_screenshot

    async def size(self):
        return 1920, 1080

    async def position(self):
        return 10, 20

    async def screenshot(self, *, region=None):
        self.region = region
        if self.fail_screenshot:
            raise RuntimeError("Separator is not found, and chunk exceed the limit")
        return {"content": [{"type": "image", "data": "tiny"}]}


@pytest.mark.asyncio
async def test_runtime_cert_pyautogui_probe_requests_small_region():
    py = _FakePyAutoGUI()
    result = await _probe_pyautogui_mcp_readonly(py, {"available": True})
    assert result["pass"] is True
    assert py.region == [0, 0, 128, 128]


@pytest.mark.asyncio
async def test_runtime_cert_pyautogui_failure_is_isolated_and_descriptive():
    py = _FakePyAutoGUI(fail_screenshot=True)
    result = await _probe_pyautogui_mcp_readonly(py, {"available": True})
    assert result["pass"] is False
    assert "chunk exceed" in result["detail"]
    assert "error" in result["evidence"]


def test_backend_text_model_nonexact_marker_is_available(monkeypatch: pytest.MonkeyPatch):
    import backend.app as backend_app
    from fastapi.testclient import TestClient

    cfg = AppConfig()
    cfg.aia.enabled = True
    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": cfg)
    monkeypatch.setattr(AIAClient, "chat_rest", lambda self, *a, **k: "Healthy Dell AIA response")
    response = TestClient(backend_app.app).post("/api/models/text/test", json={"config": "config.yaml"})
    data = response.json()
    assert response.status_code == 200
    assert data["pass"] is True
    assert data["status"] == "available"
    assert data["capability_verified"] is True
    assert data["expected_marker_seen"] is False
    assert data["contract_warning"]


def test_backend_vision_failure_still_reports_configured_model(monkeypatch: pytest.MonkeyPatch):
    import backend.app as backend_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("VISION_MODEL_NAME", "gemma-3-27b-it")
    cfg = AppConfig()
    monkeypatch.setattr(backend_app, "_cfg", lambda config="config.yaml": cfg)

    class FakeVision:
        def __init__(self, config, *, aia_config):
            pass
        async def preflight(self, *, force=False):
            return {"pass": False, "status": "error", "error": "probe failed", "attempts": []}
        def status(self):
            return {"selected_model": "", "explicit_model_candidates": ["gemma-3-27b-it"]}
        def _endpoint(self):
            return "https://configured.invalid/chat/completions"

    monkeypatch.setattr(backend_app, "VisionRuntimeBridge", FakeVision)
    response = TestClient(backend_app.app).post("/api/models/vision/test", json={"config": "config.yaml"})
    data = response.json()
    assert data["model"] == "gemma-3-27b-it"
