from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

import requests

from .aia_client import AIAClient, build_chat_payload_variants, extract_aia_response_text, extract_json_object, resolve_output_token_limit
from .security import mask_sensitive_data, mask_sensitive_string


_EXPLICIT_MODEL_ALIASES = (
    "HIP_VISION_MODEL",
    "HIP_VISION_MODELS",
    "AIA_VISION_MODEL",
    "AIA_VISION_MODELS",
    "VISION_MODEL_NAME",
    "VISION_MODEL",
    "VISION_MODELS",
    "GEMMA_MODEL_NAME",
    "GEMMA_MODEL",
)


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _split_models(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(_split_models(item))
        return list(dict.fromkeys(out))
    return list(dict.fromkeys(x.strip() for x in re.split(r"[,;\n]+", str(value)) if x.strip()))


def _extract_json(text: str) -> Dict[str, Any]:
    return extract_json_object(text)


class VisionRuntimeBridge:
    """Multimodal perception on the SAME authenticated HIP browser.

    This is deliberately a perception/judge layer, not an autonomous clicker.  It
    augments the deterministic DOM/MCP executor with screenshots during recovery
    and provides a second, visual confirmation before the five-minute loading
    watchdog is allowed to refresh Microsoft Edge.
    """

    def __init__(self, config: Any, *, aia_config: Any):
        self.config = config
        self.enabled = bool(config is not None and getattr(config, "enabled", True))
        self.require_model = bool(getattr(config, "require_model", True)) if config is not None else True
        self.use_for_recovery = bool(getattr(config, "use_for_recovery", True)) if config is not None else True
        self.use_for_loading_watchdog = bool(getattr(config, "use_for_loading_watchdog", True)) if config is not None else True
        self.loading_refresh_after_seconds = max(1, int(getattr(config, "loading_refresh_after_seconds", 300) or 300)) if config is not None else 300
        self.loading_confidence_threshold = min(1.0, max(0.0, float(getattr(config, "loading_confidence_threshold", 0.70) or 0.70))) if config is not None else 0.70
        self.timeout_seconds = max(3, int(getattr(config, "timeout_seconds", 30) or 30)) if config is not None else 30
        self.max_prompt_chars = max(4000, int(getattr(config, "max_prompt_chars", 24000) or 24000)) if config is not None else 24000
        self.max_recovery_summary_chars = max(2000, int(getattr(config, "max_recovery_summary_chars", 12000) or 12000)) if config is not None else 12000
        self.fail_closed_when_unavailable = bool(getattr(config, "fail_closed_when_unavailable", True)) if config is not None else True
        self.auto_discovery = bool(getattr(config, "auto_discovery", True)) if config is not None else True
        self.auto_candidates = _split_models(getattr(config, "auto_candidates", [])) if config is not None else []
        self.aia = AIAClient(aia_config)
        self._selected_model = ""
        self._preflight: Dict[str, Any] = {}
        self._preflight_lock = asyncio.Lock()

    def _explicit_candidates(self) -> List[str]:
        out: List[str] = []
        for name in _EXPLICIT_MODEL_ALIASES:
            out.extend(_split_models(os.getenv(name)))
        return list(dict.fromkeys(out))

    def _candidates(self) -> List[str]:
        explicit = self._explicit_candidates()
        if explicit:
            return explicit
        if not self.auto_discovery:
            return []
        env_candidates = _split_models(os.getenv("HIP_VISION_AUTO_CANDIDATES"))
        selected = _split_models(os.getenv("HIP_VISION_MODEL_SELECTED"))
        defaults = self.auto_candidates or ["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft"]
        return list(dict.fromkeys([*selected, *env_candidates, *defaults]))

    def _endpoint(self) -> str:
        direct = os.getenv("HIP_VISION_ENDPOINT") or os.getenv("AIA_VISION_ENDPOINT") or os.getenv("VISION_ENDPOINT")
        if direct and str(direct).strip():
            value = str(direct).strip().rstrip("/")
            return value if value.endswith("/chat/completions") else value + "/chat/completions"
        return str(self.aia._endpoint() or "")

    def _token(self) -> str:
        direct = os.getenv("HIP_VISION_TOKEN") or os.getenv("AIA_VISION_TOKEN") or os.getenv("VISION_TOKEN")
        if direct and str(direct).strip():
            return str(direct).strip().removeprefix("Bearer ").strip()
        return str(self.aia.token_provider.get_token() or "")

    @staticmethod
    def _probe_payload(model: str, *, completion_field: Optional[str] = None, token_limit: Optional[int] = None) -> Dict[str, Any]:
        # 64x32 PNG: left half red, right half blue. A 2-pixel probe was too
        # fragile because real multimodal preprocessing can resize/average it
        # away. This image remains tiny on the wire but is visually unambiguous.
        probe = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAAAgCAIAAAAt/+nTAAAAQklEQVR4nO3PQREAIBADMcC/Z1Bx5LMR0M7uu2btNftwRtc/KEArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAe+LxAj8B7QRwAAAAAElFTkSuQmCC"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect the image. State the color of the LEFT half and the RIGHT half. Include the words red and blue in your answer."},
                    {"type": "image_url", "image_url": {"url": probe}},
                ],
            }],
            "temperature": 0,
        }
        if completion_field and token_limit:
            payload[completion_field] = int(token_limit)
        return payload

    def _sync_preflight(self, *, force_probe: bool = False) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "pass": not self.require_model, "enabled": False}
        candidates = self._candidates()
        endpoint, token = self._endpoint(), self._token()
        if not candidates:
            return {"status": "error", "pass": False, "error": "No multimodal vision model candidate is configured.", "candidates": []}
        if not endpoint or not token:
            return {"status": "error", "pass": False, "error": "Dell AIA endpoint/token unavailable for multimodal vision.", "candidates": candidates}
        if (not force_probe) and _truthy(os.getenv("HIP_SKIP_VISION_CAPABILITY_PROBE"), False):
            self._selected_model = candidates[0]
            os.environ["HIP_VISION_MODEL_SELECTED"] = self._selected_model
            return {"status": "configured_probe_skipped", "pass": True, "model": self._selected_model, "candidates": candidates}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}
        attempts: List[Dict[str, Any]] = []
        token_limit = resolve_output_token_limit(None, vision=True)
        for model in candidates:
            base_payload = self._probe_payload(model)
            payloads = build_chat_payload_variants(base_payload, output_token_limit=token_limit)
            model_attempted = False
            for payload in payloads:
                completion_field = "max_completion_tokens" if "max_completion_tokens" in payload else ("max_tokens" if "max_tokens" in payload else "native_uncapped")
                try:
                    response = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout_seconds)
                    model_attempted = True
                    if response.status_code >= 400:
                        attempts.append({
                            "model": model, "pass": False, "http_status": response.status_code,
                            "completion_field": completion_field, "error": mask_sensitive_string(response.text[:300]),
                        })
                        if response.status_code not in {400, 422} or not token_limit:
                            break
                        continue
                    data = response.json()
                    raw = extract_aia_response_text(data)
                    norm = raw.lower()
                    ok = "red" in norm and "blue" in norm
                    attempts.append({
                        "model": model, "pass": ok, "http_status": response.status_code,
                        "completion_field": completion_field, "response": mask_sensitive_string(raw)[:500],
                    })
                    if ok:
                        self._selected_model = model
                        os.environ["HIP_VISION_MODEL_SELECTED"] = model
                        return {"status": "ok", "pass": True, "model": model, "attempts": attempts, "response_preview": mask_sensitive_string(raw)[:500]}
                    break
                except Exception as exc:
                    attempts.append({"model": model, "pass": False, "completion_field": completion_field, "error": mask_sensitive_string(str(exc))[:500]})
                    break
            if not model_attempted:
                attempts.append({"model": model, "pass": False, "error": "vision probe did not execute"})
        return {"status": "error", "pass": False, "error": "No tested Dell AIA deployment proved image understanding.", "attempts": attempts}

    async def preflight(self, *, force: bool = False) -> Dict[str, Any]:
        """Probe the configured multimodal deployment.

        Normal runtime callers use the cached capability proof.  ``force=True`` is
        reserved for the operator-facing availability button and deliberately
        performs a fresh image-understanding request even when
        HIP_SKIP_VISION_CAPABILITY_PROBE is set.
        """
        if self._preflight.get("pass") and not force:
            return dict(self._preflight)
        async with self._preflight_lock:
            if self._preflight.get("pass") and not force:
                return dict(self._preflight)
            self._preflight = mask_sensitive_data(
                await asyncio.to_thread(self._sync_preflight, force_probe=force)
            )
            return dict(self._preflight)

    def status(self) -> Dict[str, Any]:
        explicit = self._explicit_candidates()
        return {
            "enabled": self.enabled,
            "role": "multimodal perception + strict visual confirmation; never an ungoverned click executor",
            "use_for_recovery": self.use_for_recovery,
            "use_for_loading_watchdog": self.use_for_loading_watchdog,
            "loading_refresh_after_seconds": self.loading_refresh_after_seconds,
            "loading_confidence_threshold": self.loading_confidence_threshold,
            "require_model": self.require_model,
            "selected_model": self._selected_model,
            "explicit_model_candidates": explicit,
            "candidate_count": len(self._candidates()),
            "preflight": mask_sensitive_data(self._preflight),
        }

    @staticmethod
    def _data_url(image_bytes: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")

    def _sync_multimodal_json(self, *, system: str, instruction: str, image_bytes: bytes) -> Dict[str, Any]:
        model = self._selected_model or (self._candidates()[0] if self._candidates() else "")
        endpoint, token = self._endpoint(), self._token()
        if not model or not endpoint or not token:
            raise RuntimeError("multimodal model/endpoint/token unavailable")
        content = [
            {"type": "text", "text": str(instruction)[: self.max_prompt_chars]},
            {"type": "image_url", "image_url": {"url": self._data_url(image_bytes)}},
        ]
        base = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}], "temperature": 0}
        token_limit = resolve_output_token_limit(None, vision=True)
        payloads = build_chat_payload_variants(base, output_token_limit=token_limit)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "application/json"}
        last = ""
        for payload in payloads:
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout_seconds)
                if response.status_code >= 400:
                    last = f"HTTP {response.status_code}: {response.text[:500]}"
                    continue
                data = response.json()
                raw = extract_aia_response_text(data)
                if not raw:
                    last = "Dell AIA vision returned HTTP success but no usable assistant output could be extracted"
                    continue
                result = _extract_json(raw)
                if set(result.keys()) == {"raw"}:
                    # Preserve the visual answer for diagnostics, but fail the structured
                    # call instead of pretending malformed JSON satisfied the contract.
                    last = "Dell AIA vision returned non-JSON output for a strict JSON request: " + mask_sensitive_string(raw)[:500]
                    continue
                result["model"] = model
                result["http_status"] = response.status_code
                result["response_preview"] = mask_sensitive_string(raw)[:800]
                return mask_sensitive_data(result)
            except Exception as exc:
                last = mask_sensitive_string(str(exc))
        raise RuntimeError(last or "multimodal call failed")

    async def classify_loading_page(
        self,
        *,
        page: Any,
        dom_state: Optional[Mapping[str, Any]] = None,
        elapsed_seconds: float,
        reason: str = "portal loading watchdog",
    ) -> Dict[str, Any]:
        if not self.enabled or not self.use_for_loading_watchdog:
            return {"available": False, "confirmed_blocking_loading": False, "reason": "vision loading classifier disabled"}
        preflight = await self.preflight()
        if not preflight.get("pass"):
            return {"available": False, "confirmed_blocking_loading": False, "preflight": preflight, "reason": "vision model unavailable"}
        try:
            image = await page.screenshot(full_page=False, type="png")
        except TypeError:
            image = await page.screenshot(full_page=False)
        dom = mask_sensitive_data(dict(dom_state or {}))
        # Do not send large HTML or customer field values; loading geometry/text is enough.
        slim_dom = {
            "classification": dom.get("classification"),
            "active": dom.get("active"),
            "blocking_fingerprint": dom.get("blocking_fingerprint"),
            "overlays": list(dom.get("overlays") or [])[:12],
            "target_intercepted": dom.get("target_intercepted"),
            "surface_cover_ratio": dom.get("surface_cover_ratio"),
            "viewport_cover_ratio": dom.get("viewport_cover_ratio"),
        }
        instruction = json.dumps({
            "task": "Decide whether this Dell HIP Portal screenshot is still genuinely blocked by a visible loading/progress overlay.",
            "reason": str(reason)[:1000],
            "elapsed_seconds": round(float(elapsed_seconds), 2),
            "dom_loading_evidence": slim_dom,
            "rules": [
                "A small inline spinner, icon, skeleton, or passive progress indicator is NOT blocking if the main form is visibly usable.",
                "Return blocking=true only when a loading surface visibly prevents meaningful interaction with the active form/page.",
                "Do not infer hidden controls or values. Judge only the visible screenshot and supplied loading geometry.",
            ],
            "return_json": {
                "loading_visible": "boolean",
                "blocking": "boolean",
                "main_form_usable": "boolean",
                "observed_loading_text": "short string",
                "confidence": "0..1",
                "reason": "short string",
            },
        }, ensure_ascii=False)[: self.max_prompt_chars]
        system = "You are the multimodal page-health judge for Dell HIP Portal. Return ONLY strict JSON. Never authorize Save/Create/Delete/Deploy."
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._sync_multimodal_json, system=system, instruction=instruction, image_bytes=image),
                timeout=self.timeout_seconds + 5,
            )
        except Exception as exc:
            return {"available": False, "confirmed_blocking_loading": False, "error": mask_sensitive_string(str(exc))[:1000], "preflight": preflight}
        confidence = 0.0
        try:
            confidence = float(result.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        loading_visible = _truthy(result.get("loading_visible"), False)
        blocking = _truthy(result.get("blocking"), False)
        confirmed = bool(loading_visible and blocking and confidence >= self.loading_confidence_threshold)
        return mask_sensitive_data({
            "available": True,
            "confirmed_blocking_loading": confirmed,
            "loading_visible": loading_visible,
            "blocking": blocking,
            "main_form_usable": _truthy(result.get("main_form_usable"), False),
            "observed_loading_text": str(result.get("observed_loading_text") or "")[:500],
            "confidence": confidence,
            "threshold": self.loading_confidence_threshold,
            "reason": str(result.get("reason") or "")[:1200],
            "model": result.get("model") or self._selected_model,
            "elapsed_seconds": float(elapsed_seconds),
        })

    async def locate_visual_target(
        self, *, page: Any, target_label: str, target_kind: str = "button", context: str = ""
    ) -> Dict[str, Any]:
        """Locate one visible structural target using normalized viewport coordinates.

        This is a perception primitive only. It never clicks. The caller must apply
        its own confidence/risk gate and independently verify the post-click effect.
        """
        if not self.enabled or not self.use_for_recovery or page is None:
            return {"available": False, "target_visible": False, "reason": "vision target locator disabled or page unavailable"}
        preflight = await self.preflight()
        if not preflight.get("pass"):
            return {"available": False, "target_visible": False, "reason": "vision preflight failed", "preflight": preflight}
        try:
            image = await page.screenshot(full_page=False, type="png")
        except TypeError:
            image = await page.screenshot(full_page=False)
        instruction = json.dumps({
            "task": "Locate exactly one visible UI control in the current Dell HIP browser viewport.",
            "target_label": str(target_label or "")[:500],
            "target_kind": str(target_kind or "control")[:100],
            "context": str(context or "")[:1800],
            "rules": [
                "Return coordinates normalized to the CURRENT VISIBLE BROWSER VIEWPORT: 0.0 is left/top and 1.0 is right/bottom.",
                "Choose the center of the exact requested control, not nearby text, a row-level Add, pagination, navigation, footer, cookie control, or unrelated action.",
                "For '+ Add' page openers, prefer the top-right page-level toolbar Add for the active HIP section.",
                "If the exact target is not clearly visible or there are multiple ambiguous matches, set target_visible=false.",
                "Do not infer hidden controls or customer values.",
            ],
            "return_json": {
                "target_visible": "boolean",
                "observed_label": "short string",
                "x_ratio": "number 0..1",
                "y_ratio": "number 0..1",
                "confidence": "number 0..1",
                "reason": "short string",
            },
        }, ensure_ascii=False)[: self.max_prompt_chars]
        system = "You are a precise visual UI target locator for Dell HIP Portal. Return ONLY strict JSON. Locate; never click or authorize mutations."
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._sync_multimodal_json, system=system, instruction=instruction, image_bytes=image),
                timeout=self.timeout_seconds + 5,
            )
        except Exception as exc:
            return {"available": False, "target_visible": False, "error": mask_sensitive_string(str(exc))[:1000]}
        try:
            x = float(result.get("x_ratio"))
            y = float(result.get("y_ratio"))
            confidence = float(result.get("confidence") or 0.0)
        except Exception:
            x, y, confidence = -1.0, -1.0, 0.0
        visible = _truthy(result.get("target_visible"), False) and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
        return mask_sensitive_data({
            "available": True,
            "target_visible": bool(visible),
            "observed_label": str(result.get("observed_label") or "")[:500],
            "x_ratio": x, "y_ratio": y, "confidence": confidence,
            "reason": str(result.get("reason") or "")[:1200],
            "model": result.get("model") or self._selected_model,
            "source": "vision",
        })

    async def recovery_context(self, *, page: Any, task: str) -> Dict[str, Any]:
        if not self.enabled or not self.use_for_recovery or page is None:
            return {"available": False, "reason": "vision recovery disabled or page unavailable"}
        preflight = await self.preflight()
        if not preflight.get("pass"):
            return {"available": False, "reason": "vision preflight failed", "preflight": preflight}
        try:
            image = await page.screenshot(full_page=False, type="png")
        except TypeError:
            image = await page.screenshot(full_page=False)
        instruction = json.dumps({
            "task": str(task or "")[:3000],
            "instruction": "Visually summarize only actionable recovery evidence on the current Dell HIP form. Do not guess customer values and do not propose final mutations.",
            "return_json": {
                "active_surface": "short string",
                "loading_visible": "boolean",
                "blocking_overlay_visible": "boolean",
                "visible_error_or_validation": "short string",
                "likely_interactable_labels": ["short labels"],
                "visual_mismatch_or_obstruction": "short string",
                "confidence": "0..1",
            },
        }, ensure_ascii=False)[: self.max_prompt_chars]
        system = "You are the multimodal recovery observer for Dell HIP Portal. Return ONLY strict JSON. Observe; never authorize or execute Save/Create/Delete/Deploy."
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._sync_multimodal_json, system=system, instruction=instruction, image_bytes=image),
                timeout=self.timeout_seconds + 5,
            )
            return mask_sensitive_data({"available": True, **result})
        except Exception as exc:
            return {"available": False, "error": mask_sensitive_string(str(exc))[:1000]}
