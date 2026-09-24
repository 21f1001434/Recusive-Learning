from __future__ import annotations

from dataclasses import dataclass

from hip_id_agent.section_judge import DualModelSectionJudge, SectionJudgePolicy


VISION_ENV_NAMES = [
    "HIP_VISION_MODEL", "HIP_VISION_MODELS", "AIA_VISION_MODEL", "AIA_VISION_MODELS",
    "VISION_MODEL_NAME", "VISION_MODEL", "VISION_MODELS", "GEMMA_MODEL_NAME", "GEMMA_MODEL",
    "HIP_VISION_AUTO_CANDIDATES", "HIP_VISION_ENDPOINT", "AIA_VISION_ENDPOINT", "VISION_ENDPOINT",
    "HIP_VISION_TOKEN", "AIA_VISION_TOKEN", "VISION_TOKEN", "HIP_SKIP_VISION_CAPABILITY_PROBE",
]


def _judge(monkeypatch):
    for name in VISION_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    j = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=True))
    monkeypatch.setattr(j, "_vision_endpoint", lambda: "https://example.invalid/chat/completions")
    monkeypatch.setattr(j, "_vision_token", lambda: "test-token")
    return j


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    def json(self):
        return self.payload


def test_text_model_is_not_a_direct_vision_fallback_but_bounded_discovery_exists(monkeypatch):
    j = _judge(monkeypatch)
    monkeypatch.setenv("MODEL_NAME", "gpt-oss-120b")
    assert j._vision_model() == ""
    assert j._vision_model_candidates() == ["gemma-3-27b-it", "pixtral-12b-2409"]


def test_auto_discovery_probes_and_selects_working_candidate(monkeypatch):
    j = _judge(monkeypatch)
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "gemma-3-27b-it":
            return FakeResponse(404, {}, "deployment unavailable")
        return FakeResponse(200, {"choices": [{"message": {"content": '{"left":"red","right":"blue"}'}}]})

    monkeypatch.setattr("hip_id_agent.section_judge.requests.post", fake_post)
    result = j.vision_preflight()
    assert result["pass"] is True
    assert result["model"] == "pixtral-12b-2409"
    assert result["selection_source"] == "auto_discovery_probe"
    assert j._vision_model() == "pixtral-12b-2409"
    assert "gemma-3-27b-it" in calls and "pixtral-12b-2409" in calls


def test_legacy_gemma_alias_is_supported(monkeypatch):
    j = _judge(monkeypatch)
    monkeypatch.setenv("GEMMA_MODEL_NAME", "company-gemma-vision")
    assert j._explicit_vision_candidates() == ["company-gemma-vision"]


def test_auto_discovery_can_be_disabled_and_fails_before_portal(monkeypatch):
    j = _judge(monkeypatch)
    monkeypatch.setenv("HIP_VISION_AUTO_DISCOVERY", "false")
    result = j.vision_preflight()
    assert result["pass"] is False
    assert "No multimodal model candidate" in result["error"]
    assert "HIP_VISION_MODEL" in result["powershell"]


def test_vision_endpoint_override_accepts_base_url(monkeypatch):
    j = _judge(monkeypatch)
    # restore the real method after _judge replaced it
    monkeypatch.undo()
    for name in VISION_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    j = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=True))
    monkeypatch.setenv("HIP_VISION_ENDPOINT", "https://aia.example/genai/dev/v1")
    assert j._vision_endpoint() == "https://aia.example/genai/dev/v1/chat/completions"
