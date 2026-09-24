from hip_id_agent.config import AIAConfig
from hip_id_agent.aia_client import AIAClient
from hip_id_agent.llm_form_planner import llm_form_planner_enabled


def test_existing_dell_aia_env_aliases_enable_planner(monkeypatch):
    monkeypatch.delenv("HIP_USE_LLM_FORM_PLANNER", raising=False)
    monkeypatch.setenv("USE_DELL_SSO", "true")
    monkeypatch.setenv("DELL_AUTH_MODE", "auto")
    monkeypatch.setenv("MODEL_NAME", "gpt-oss-120b")
    monkeypatch.setenv("BASE_URL", "https://aia.gateway.dell.com/genai/dev/v1")
    monkeypatch.setenv("CLIENT_ID", "dummy-client")
    monkeypatch.setenv("CLIENT_SECRET", "dummy-secret")

    assert llm_form_planner_enabled(AIAConfig(enabled=False)) is True


def test_aia_client_uses_existing_base_url_and_model_name(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gpt-oss-120b")
    monkeypatch.setenv("BASE_URL", "https://aia.gateway.dell.com/genai/dev/v1")
    monkeypatch.setenv("DELL_AUTH_MODE", "auto")
    c = AIAClient(AIAConfig(enabled=True))

    summary = c.provider_summary()
    assert summary["model"] == "gpt-oss-120b"
    assert summary["endpoint"] == "https://aia.gateway.dell.com/genai/dev/v1/chat/completions"
    assert summary["auth_mode"] == "auto"
    assert summary["uses_existing_env_aliases"] is True
