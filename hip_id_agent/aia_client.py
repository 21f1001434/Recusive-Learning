from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .config import AIAConfig
from .security import mask_sensitive_data
from .autogen_runtime import assert_autogen_075, autogen_runtime_status, autogen_strict_required


def _first_env(*names: str, default: str = "") -> str:
    """Return first non-empty environment variable from aliases.

    This lets the HIP agent reuse existing Dell AIA .env files such as:
    MODEL_NAME, BASE_URL, DELL_AUTH_MODE, USE_DELL_SSO, CLIENT_ID, CLIENT_SECRET.
    """
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default




_HARMONY_FINAL_MARKERS = ("<|channel|>final<|message|>", "assistantfinal")


def strip_harmony_analysis(text: str) -> str:
    """Keep only gpt-oss's final answer when its channels arrive unsplit.

    gpt-oss (e.g. gpt-oss-120b) writes an ``analysis`` channel before the
    ``final`` one.  A gateway without a reasoning parser returns both in one
    string ("analysis...assistantfinal{...}"); the reasoning often contains
    JSON-like fragments that must not be mistaken for the answer.
    """
    raw = str(text or "")
    cut = -1
    for marker in _HARMONY_FINAL_MARKERS:
        index = raw.rfind(marker)
        if index >= 0:
            cut = max(cut, index + len(marker))
    if cut < 0:
        return raw
    final = raw[cut:]
    for end in ("<|return|>", "<|end|>"):
        final = final.split(end, 1)[0]
    return final.strip()


def extract_aia_response_text(data: Any) -> str:
    """Extract the usable assistant answer from Dell AIA response variants.

    Final assistant/output content is preferred over reasoning/analysis content.
    For reasoning models this prevents intermediate reasoning from being merged
    with the final JSON/text answer. Reasoning is used only when no final answer
    text exists at all.
    """
    final_parts: list[str] = []
    reasoning_parts: list[str] = []
    final_keys = (
        "content", "text", "output_text", "generated_text", "answer",
        "completion", "response", "result", "assistant", "message", "delta",
        "value", "output", "outputs", "results", "candidates", "data", "parts",
    )
    reasoning_keys = ("reasoning_content", "reasoning", "analysis", "thinking", "thought")

    def add(value: Any, *, reasoning: bool = False) -> None:
        if value is None:
            return
        if isinstance(value, str):
            text = value.strip()
            if text:
                (reasoning_parts if reasoning else final_parts).append(text)
            return
        if isinstance(value, (int, float, bool)):
            return
        if isinstance(value, list):
            for item in value:
                add(item, reasoning=reasoning)
            return
        if not isinstance(value, dict):
            return
        type_hint = str(value.get("type") or "").strip().lower()
        local_reasoning = reasoning or any(x in type_hint for x in ("reason", "analysis", "thinking"))
        for key in reasoning_keys:
            if key in value:
                add(value.get(key), reasoning=True)
        for key in final_keys:
            if key in value:
                add(value.get(key), reasoning=local_reasoning)

    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                add(choice)
        for key in ("output_text", "output", "outputs", "content", "response", "result", "data", "candidates"):
            if key in data:
                add(data.get(key))
    else:
        add(data)

    selected = final_parts if final_parts else reasoning_parts
    out: list[str] = []
    seen: set[str] = set()
    for item in selected:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return strip_harmony_analysis("\n".join(out).strip())


def resolve_output_token_limit(explicit: Optional[int] = None, *, vision: bool = False, probe: bool = False) -> Optional[int]:
    """Return a positive optional output-token limit; default is uncapped.

    When this returns None the request omits both max_tokens and
    max_completion_tokens, allowing the Dell AIA deployment to use its native
    output allowance. Positive environment overrides remain available for
    deployments that explicitly require a client-side limit.
    """
    if explicit is not None:
        try:
            value = int(explicit)
            return value if value > 0 else None
        except Exception:
            return None
    names: list[str] = ["AIA_VISION_MAX_OUTPUT_TOKENS" if vision else "AIA_TEXT_MAX_OUTPUT_TOKENS", "AIA_MAX_OUTPUT_TOKENS"]
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        text = str(raw).strip().lower()
        if text in {"", "0", "none", "null", "unlimited", "uncapped", "auto", "native", "off", "false"}:
            return None
        try:
            value = int(text)
            return value if value > 0 else None
        except Exception:
            continue
    return None


def build_chat_payload_variants(base: Dict[str, Any], *, output_token_limit: Optional[int]) -> list[Dict[str, Any]]:
    if not output_token_limit:
        return [dict(base)]
    limit = int(output_token_limit)
    return [
        {**base, "max_completion_tokens": limit},
        {**base, "max_tokens": limit},
    ]


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extract the first valid JSON object from model output."""
    raw = strip_harmony_analysis(str(text or "")).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[idx:])
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return {"raw": raw[:12000]}


def _truthy_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _normalize_auth_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower().replace("-", "_")
    aliases = {
        "": "auto",
        "automatic": "auto",
        "clientcredentials": "client_credentials",
        "client_credentials": "client_credentials",
        "service_account": "client_credentials",
        "serviceaccount": "client_credentials",
        "browser_sso": "sso",
        "dell_sso": "sso",
        "sso": "sso",
        "env": "env",
        "token": "env",
        "static": "env",
        "command": "command",
        "cmd": "command",
        "auto": "auto",
    }
    return aliases.get(m, "auto")


class DellAIATokenProvider:
    """Token provider compatible with Dell AIA environments.

    Supported modes:
      - env: read token from AIA_TOKEN or configured env var.
      - command: run token_command and use stdout.
      - client_credentials: use aia_auth.auth.client_credentials(client_id, client_secret).
      - sso: use aia_auth.auth.sso() for browser SSO token.
      - auto: env -> command -> client_credentials if id/secret exist -> sso.

    Nothing is stored on disk. Tokens are only held in memory and masked in logs.
    """

    def __init__(self, config: AIAConfig, skew_seconds: int = 60):
        self.config = config
        self.skew_seconds = skew_seconds
        self._token: Optional[str] = None
        self._valid_until: float = 0.0

    def get_token(self) -> Optional[str]:
        mode = _normalize_auth_mode(_first_env("DELL_AUTH_MODE", "AIA_AUTH_MODE", "HIP_AIA_AUTH_MODE", default=self.config.auth_mode))
        if mode in {"env", "auto"}:
            token = (
                os.getenv(self.config.token_env_var)
                or os.getenv("AIA_STATIC_BEARER_TOKEN")
                or os.getenv("DELL_AIA_TOKEN")
                or os.getenv("AIA_BEARER_TOKEN")
                or os.getenv("BEARER_TOKEN")
            )
            if token:
                return token.strip().removeprefix("Bearer ").strip()
            if mode == "env":
                return None
        if mode in {"command", "auto"} and self.config.token_command:
            token = self._token_from_command()
            if token:
                return token
            if mode == "command":
                return None
        if not self.config.use_aia_auth_package:
            return None
        if mode == "auto":
            client_id = _first_env(self.config.client_id_env_var, "CLIENT_ID", "AIA_CLIENT_ID", "DELL_AIA_CLIENT_ID", "HIP_CLIENT_ID")
            client_secret = _first_env(self.config.client_secret_env_var, "CLIENT_SECRET", "AIA_CLIENT_SECRET", "DELL_AIA_CLIENT_SECRET", "HIP_CLIENT_SECRET")
            mode = "client_credentials" if client_id and client_secret else "sso"
        if mode in {"client_credentials", "sso"}:
            return self._token_from_aia_auth(mode)
        return None

    def _token_from_command(self) -> Optional[str]:
        try:
            proc = subprocess.run(
                self.config.token_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            return None
        return None

    def _token_from_aia_auth(self, mode: str) -> Optional[str]:
        now = time.time()
        if self._token and now < (self._valid_until - self.skew_seconds):
            return self._token
        try:
            from aia_auth import auth as aia_auth  # type: ignore
        except Exception:
            return None
        try:
            if mode == "client_credentials":
                client_id = _first_env(self.config.client_id_env_var, "CLIENT_ID", "AIA_CLIENT_ID", "DELL_AIA_CLIENT_ID", "HIP_CLIENT_ID")
                client_secret = _first_env(self.config.client_secret_env_var, "CLIENT_SECRET", "AIA_CLIENT_SECRET", "DELL_AIA_CLIENT_SECRET", "HIP_CLIENT_SECRET")
                if not client_id or not client_secret:
                    return None
                resp = aia_auth.client_credentials(client_id, client_secret)
            else:
                resp = aia_auth.sso()
            self._token = getattr(resp, "token", None) or (resp.get("token") if isinstance(resp, dict) else None)
            expires_in = int(getattr(resp, "expires_in", 1800) or (resp.get("expires_in", 1800) if isinstance(resp, dict) else 1800))
            self._valid_until = now + expires_in
            return self._token
        except Exception:
            return None


class AIAClient:
    """Optional Dell AIA/gpt-oss-120b adapter.

    Deterministic UI/network extraction is always tried first. AIA is only used as
    a fallback when candidates are missing or low-confidence.
    """

    def __init__(self, config: AIAConfig):
        self.config = config
        self.token_provider = DellAIATokenProvider(config)

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def _endpoint(self) -> Optional[str]:
        # Support both the HIP-specific names and the existing Dell AIA .env names
        # shown in the user's working reference: BASE_URL, MODEL_NAME, DELL_AUTH_MODE.
        direct = _first_env(self.config.endpoint_env_var, "HIP_LLM_ENDPOINT", "AIA_ENDPOINT", "DELL_AIA_ENDPOINT")
        if direct:
            return direct.rstrip("/")
        base = _first_env(
            "AIA_BASE_URL",
            "DELL_AIA_BASE_URL",
            self.config.base_url_env_var,
            "OPENAI_BASE_URL",
            "BASE_URL",
        )
        if base:
            b = base.rstrip("/")
            return b if b.endswith("/chat/completions") else b + "/chat/completions"
        return None

    def provider_summary(self) -> Dict[str, Any]:
        endpoint = self._endpoint() or ""
        return {
            "provider_lock": os.getenv("MODEL_PROVIDER_LOCK", "DELL_AIA_ONLY"),
            "llm_provider": "Dell AIA GenAI Gateway",
            "agent_framework": "Microsoft AutoGen AgentChat 0.7.5 AssistantAgent",
            "autogen_runtime": autogen_runtime_status(verify_imports=False),
            "model": self._model(),
            "endpoint_configured": bool(endpoint),
            "endpoint": endpoint,
            "auth_mode": _normalize_auth_mode(_first_env("DELL_AUTH_MODE", "AIA_AUTH_MODE", "HIP_AIA_AUTH_MODE", default=self.config.auth_mode)),
            "uses_existing_env_aliases": True,
            "non_dell_llm_fallback": False,
        }

    def _model(self) -> str:
        return _first_env("HIP_MODEL_ROUTER_SELECTED_TEXT", "AIA_TEXT_MODEL", "MODEL_NAME", "HIP_LLM_MODEL", "AIA_MODEL", "TEXT_MODEL", "TEXT_MODELS", default=self.config.model or "gpt-oss-120b").split(",")[0].strip()

    def chat_rest(self, messages: list[dict[str, Any]], temperature: float = 0.0, max_tokens: Optional[int] = None, model: Optional[str] = None) -> str:
        if os.getenv("TEMPERATURE") not in {None, ""}:
            try:
                temperature = float(str(os.getenv("TEMPERATURE") or "").strip())
            except Exception:
                pass
        if not self.enabled and os.getenv("HIP_USE_LLM_FORM_PLANNER", "").lower() not in {"1", "true", "yes", "on"}:
            raise RuntimeError("AIA disabled")
        endpoint = self._endpoint()
        token = self.token_provider.get_token()
        if not endpoint or not token:
            raise RuntimeError("AIA endpoint/token missing")
        output_token_limit = resolve_output_token_limit(max_tokens, vision=False)
        base_payload = {"model": str(model or self._model()), "messages": messages, "temperature": temperature}
        payloads = build_chat_payload_variants(base_payload, output_token_limit=output_token_limit)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}
        last = ""
        for payload in payloads:
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=self.config.timeout_seconds)
                if resp.status_code >= 400:
                    last = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    continue
                data = resp.json()
                extracted = extract_aia_response_text(data)
                if extracted:
                    return extracted
                keys = sorted(str(k) for k in data.keys()) if isinstance(data, dict) else []
                choice_keys: list[str] = []
                message_keys: list[str] = []
                finish_reason = ""
                if isinstance(data, dict) and isinstance(data.get("choices"), list) and data.get("choices"):
                    first_choice = data.get("choices")[0]
                    if isinstance(first_choice, dict):
                        choice_keys = sorted(str(k) for k in first_choice.keys())
                        finish_reason = str(first_choice.get("finish_reason") or "")
                        first_message = first_choice.get("message")
                        if isinstance(first_message, dict):
                            message_keys = sorted(str(k) for k in first_message.keys())
                detail = []
                if keys:
                    detail.append(f"response_keys={keys[:20]}")
                if choice_keys:
                    detail.append(f"choice_keys={choice_keys[:20]}")
                if message_keys:
                    detail.append(f"message_keys={message_keys[:20]}")
                if finish_reason:
                    detail.append(f"finish_reason={finish_reason}")
                raise RuntimeError(
                    "Dell AIA returned HTTP success but no assistant text could be extracted"
                    + (("; " + "; ".join(detail)) if detail else "")
                )
            except Exception as exc:
                last = str(exc)
        raise RuntimeError(last or "Dell AIA chat call failed")

    def autogen_reply(self, system: str, task: str, model: Optional[str] = None) -> str:
        """Use AutoGen against Dell AIA without leaking coroutines or clients.

        When invoked from an already-running asyncio loop, the AutoGen coroutine
        is executed in a dedicated worker thread. This avoids creating an
        un-awaited coroutine and prevents httpx cleanup after the main loop has
        already closed.
        """
        strict_autogen = autogen_strict_required()
        try:
            if strict_autogen:
                assert_autogen_075(verify_imports=True)
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            from autogen_agentchat.agents import AssistantAgent
            from autogen_ext.models.openai import OpenAIChatCompletionClient

            async def _run() -> str:
                endpoint = self._endpoint()
                if not endpoint:
                    raise RuntimeError("AIA endpoint missing")
                base_url = endpoint.rsplit("/chat/completions", 1)[0] if endpoint.endswith("/chat/completions") else endpoint
                model_client = OpenAIChatCompletionClient(
                    model=str(model or self._model()),
                    base_url=base_url,
                    api_key=self.token_provider.get_token() or "",
                    model_info={
                        "vision": False,
                        "function_calling": True,
                        "json_output": True,
                        "structured_output": True,
                        "family": "unknown",
                    },
                )
                try:
                    agent = AssistantAgent(name="hip_portal_form_planner", model_client=model_client, system_message=system)
                    result = await agent.run(task=task)
                    if hasattr(result, "messages") and result.messages:
                        last = result.messages[-1]
                        return getattr(last, "content", str(last))
                    return str(result)
                finally:
                    close = getattr(model_client, "close", None)
                    if callable(close):
                        closed = close()
                        if hasattr(closed, "__await__"):
                            await closed

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_run())
            # A loop is active in this thread. Run the complete coroutine and its
            # cleanup in a separate thread with its own event loop.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="hip-aia-autogen") as pool:
                return pool.submit(lambda: asyncio.run(_run())).result(timeout=max(30, int(self.config.timeout_seconds) + 30))
        except Exception:
            if strict_autogen:
                raise
            return self.chat_rest([{"role": "system", "content": system}, {"role": "user", "content": task}], model=model)

    def json_decision(self, system: str, task: str, model: Optional[str] = None) -> Dict[str, Any]:
        text = self.autogen_reply(system, task, model=model)
        return extract_json_object(text)

    def extract_ids_from_snapshot(self, *, object_type: str, query: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {"used": False, "reason": "AIA disabled"}
        endpoint = self._endpoint()
        token = self.token_provider.get_token()
        if not endpoint or not token:
            return {"used": False, "reason": "AIA endpoint/token missing. Configure AIA_ENDPOINT or OPENAI_BASE_URL and token/aia-auth."}

        safe_snapshot = mask_sensitive_data(snapshot)
        snapshot_text = json.dumps(safe_snapshot, ensure_ascii=False, default=str)[: self.config.max_snapshot_chars]
        prompt = f"""
You are extracting Dell HIP/BizLink Portal object IDs from browser evidence.
Object type to extract: {object_type}
Search query/name/code: {query}

Rules:
- Prefer IDs from network JSON responses when a row/name matches the query.
- For partner use partnerId, partner_id, id, uuid, identifier only when it belongs to a partner record.
- For system use systemId, domainId, system_id, id, uuid only when it belongs to a system/domain record.
- Do not invent IDs. Return null when uncertain.
- Return strict JSON only with keys: object_id, name, confidence, evidence.

Evidence snapshot:
{snapshot_text}
""".strip()
        payload = {
            "model": self._model(),
            "messages": [
                {"role": "system", "content": "Return strict JSON only. No markdown. Do not reveal secrets."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"},
                timeout=self.config.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            content = extract_aia_response_text(data) or json.dumps(data, ensure_ascii=False, default=str)
            content = str(content).strip().strip("`")
            if content.lower().startswith("json"):
                content = content[4:].strip()
            parsed = json.loads(content)
            parsed["used"] = True
            return mask_sensitive_data(parsed)
        except Exception as exc:
            return {"used": False, "reason": f"AIA extraction failed: {exc}"}
