from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .models import utc_now
from .safe_io import safe_write_json, safe_mkdir
from .security import is_secret_target, mask_sensitive_data, mask_sensitive_string
from .website_understanding import WebsiteUnderstandingEngine
from .website_world_model import WebsiteWorldModelMemory
from .visual_intelligence_overlay import VisualIntelligenceOverlay, overlay_legend, public_overlay_telemetry


LIVE_VIEW_FILENAME = "agent_live_view.json"


def _clean(value: Any, limit: int = 500) -> str:
    return mask_sensitive_string(re.sub(r"\s+", " ", str(value or "")).strip())[:limit]


def _safe_value(target: str, value: Any) -> str:
    if value is None:
        return ""
    if is_secret_target(target) or is_secret_target(str(value)):
        return "***MASKED***"
    return _clean(value, 500)


def _candidate_rows(resolution: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected_id = str(resolution.get("semantic_control_id") or "")
    rows: list[dict[str, Any]] = []
    ranked = ((resolution.get("evidence") or {}).get("ranked_candidates") or [])
    for index, raw in enumerate(ranked[:8], start=1):
        if not isinstance(raw, Mapping):
            continue
        sid = str(raw.get("semantic_control_id") or "")
        selected = bool(sid and sid == selected_id) or (index == 1 and not selected_id)
        reasons = [str(x) for x in (raw.get("reasons") or []) if str(x).strip()]
        rejection = ""
        if not selected:
            if raw.get("anchored") is False:
                rejection = "not the current vetted live target"
            elif reasons:
                rejection = reasons[0]
            else:
                rejection = "lower semantic score"
        rows.append(mask_sensitive_data({
            "rank": index,
            "selected": selected,
            "semantic_control_id": sid,
            "label": _clean(raw.get("label"), 300),
            "section": _clean(raw.get("section"), 300),
            "role": _clean(raw.get("role"), 120),
            "score": raw.get("score"),
            "anchored": raw.get("anchored"),
            "evidence_reasons": reasons[:8],
            "rejection_reason": rejection,
            # Selectors are intentionally excluded from this human live view.
            "selector_stored": False,
        }))
    return rows


class AgentLiveViewRecorder:
    """Browser-Use-style, user-visible execution evidence for one HIP run.

    This is observability, not planner chain-of-thought. It records only concrete
    evidence: current foreground surface, intended action, ranked semantic targets,
    selected target, expected value (masked when sensitive), executor used and the
    independently verified effect. CSS/XPath/coordinates are not persisted here.
    """

    SCHEMA_VERSION = "hip.agent-live-view.v2"

    def __init__(self, *, config: Any, run_dir: str | Path) -> None:
        self.config = config
        self.run_dir = Path(run_dir)
        policy = getattr(config, "portal_learning", None)
        self.enabled = bool(getattr(policy, "agent_live_view_enabled", True))
        self.capture_screenshots = bool(getattr(policy, "agent_live_view_capture_screenshots", True))
        self.capture_website_summary = bool(getattr(policy, "agent_live_view_capture_website_summary", True))
        self.history_limit = max(20, int(getattr(policy, "agent_live_view_history_limit", 120) or 120))
        self.root = self.run_dir / "agent_live_view"
        self.shots = self.root / "screenshots"
        safe_mkdir(self.shots, parents=True, exist_ok=True)
        self.path = self.run_dir / LIVE_VIEW_FILENAME
        self.website_understanding = WebsiteUnderstandingEngine(config=config)
        memory_root = Path(getattr(getattr(config, "reporting", None), "memory_dir", "./data/hip_memory"))
        brain_cfg = getattr(config, "brain", None)
        brain_dir = str(getattr(brain_cfg, "directory", "portal_brain") or "portal_brain")
        self.world_model = WebsiteWorldModelMemory(
            memory_root / brain_dir / "website_world_model",
            config=brain_cfg,
        )
        self.visual_overlay = VisualIntelligenceOverlay(config=config)
        # Runtime-only locator + perception keys. Never serialized to agent_live_view.json.
        self._visual_runtime: Dict[str, Dict[str, Any]] = {}
        self._sequence = 0
        if not self.path.exists():
            self._persist({
                "schema_version": self.SCHEMA_VERSION,
                "available": self.enabled,
                "updated_at": utc_now(),
                "current": {},
                "history": [],
                "overlay_legend": overlay_legend(),
                "safety": {
                    "model_chain_of_thought_stored": False,
                    "secrets_masked": True,
                    "selectors_stored": False,
                    "coordinates_stored": False,
                    "overlay_pointer_events": "none",
                    "purpose": "show concrete browser target selection, governed acting state, and independently verified effects",
                },
            })

    def _read(self) -> Dict[str, Any]:
        # V243R21: the recorder is the file's only writer, so its state is kept in
        # memory instead of being re-read (several MB) for every event.
        cached = getattr(self, "_state", None)
        if isinstance(cached, dict):
            return cached
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            data = data if isinstance(data, dict) else {}
        except Exception:
            data = {}
        self._state = data
        return data

    def _persist(self, state: Mapping[str, Any]) -> None:
        # History entries are masked once, when appended; only the rest is masked here.
        payload = mask_sensitive_data({k: v for k, v in dict(state).items() if k != "history"})
        payload["history"] = list(state.get("history") or [])
        payload["schema_version"] = self.SCHEMA_VERSION
        payload["updated_at"] = utc_now()
        self._state = payload
        safe_write_json(self.path, payload, mask=False)

    # What the decision timeline shows; the full evidence stays in ``current``.
    _HISTORY_KEYS = (
        "event", "at", "action_id", "phase", "page_url", "page_url_after", "intent", "action", "expected_value",
        "active_surface", "selected_control", "execution", "verification", "screenshot_relative_path", "screenshot_stage",
    )

    def _append(self, current: Mapping[str, Any]) -> None:
        state = dict(self._read() or {
            "schema_version": self.SCHEMA_VERSION,
            "available": self.enabled,
            "history": [],
        })
        masked = mask_sensitive_data(dict(current))
        entry = {k: masked[k] for k in self._HISTORY_KEYS if k in masked}
        stage = (masked.get("visual_overlay") or {}).get("stage") if isinstance(masked.get("visual_overlay"), Mapping) else None
        if stage:
            entry["visual_overlay"] = {"stage": stage}
        history = list(state.get("history") or [])
        history.append(entry)
        if len(history) > self.history_limit:
            history = history[-self.history_limit :]
        state["current"] = masked
        state["history"] = history
        self._persist(state)

    async def _screenshot(self, *, page: Any, locator: Any, action_id: str, label: str, suffix: str) -> str:
        """Capture the current page exactly as the visual-overlay engine rendered it.

        V234 deliberately does not create a second screenshot-only target box here.
        The browser overlay itself is authoritative for observability and is always
        pointer-events:none. `locator` and `label` stay in the signature for API
        compatibility but are never persisted as geometry or selectors.
        """
        if not self.enabled or not self.capture_screenshots or page is None:
            return ""
        self._sequence += 1
        filename = f"{self._sequence:05d}_{action_id or 'action'}_{suffix}.png"
        path = self.shots / filename
        try:
            await page.screenshot(path=str(path), full_page=False)
            return str(path.relative_to(self.run_dir)).replace("\\", "/")
        except Exception:
            return ""

    async def record_selection(
        self,
        *,
        page: Any,
        locator: Any,
        action_id: str,
        phase: str,
        action: str,
        intent: str,
        expected_value: Any,
        resolution: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        candidate = dict(resolution.get("candidate") or {}) if isinstance(resolution.get("candidate"), Mapping) else {}
        label = _clean(candidate.get("label") or intent, 300)
        surface_summary: Dict[str, Any] = {}
        current_options: list[str] = []
        if self.capture_website_summary and page is not None:
            try:
                model = await self.website_understanding.capture(
                    page=page,
                    phase=phase,
                    stage="agent_live_view_pre_action",
                    output_dir=None,
                    include_registered_listeners=False,
                )
                active = model.get("active_surface") if isinstance(model.get("active_surface"), Mapping) else {}
                surface_summary = {
                    "label": _clean(active.get("label"), 300),
                    "role": _clean(active.get("role"), 80),
                    "tag": _clean(active.get("tag"), 80),
                    "aria_modal": str(active.get("ariaModal") or ""),
                    "understanding_confidence": ((model.get("understanding_gate") or {}).get("confidence")),
                    "selected_tabs": [
                        _clean(t.get("text"), 200)
                        for t in (model.get("tabs") or [])
                        if isinstance(t, Mapping) and t.get("selected")
                    ][:12],
                }
                # Show the currently mounted choices for the chosen semantic control.
                for row in (model.get("option_catalog") or {}).values():
                    if not isinstance(row, Mapping):
                        continue
                    same_label = _clean(row.get("label"), 300).lower() == label.lower()
                    same_section = not candidate.get("section") or _clean(row.get("section"), 300).lower() == _clean(candidate.get("section"), 300).lower()
                    if same_label and same_section:
                        current_options = [_clean(o.get("text"), 250) for o in (row.get("options") or []) if isinstance(o, Mapping) and o.get("visible")][:100]
                        break
            except Exception:
                pass
        overlay_runtime: Dict[str, Any] = {}
        try:
            overlay_runtime = await self.visual_overlay.render_selection(
                page=page, locator=locator, resolution=resolution, label=label or intent,
            )
        except Exception:
            overlay_runtime = {}
        # Locator and seen keys are intentionally runtime-only.
        self._visual_runtime[str(action_id or "")] = {
            "locator": locator,
            "label": label or intent,
            "seen_keys": list(overlay_runtime.get("_seen_keys") or []),
        }
        screenshot = await self._screenshot(
            page=page,
            locator=locator,
            action_id=action_id,
            label=label or intent,
            suffix="selected",
        )
        evidence = resolution.get("evidence") if isinstance(resolution.get("evidence"), Mapping) else {}
        memory_hints = []
        try:
            memory_hints = self.world_model.planner_hints(
                phase=phase, action=action, label=label or intent,
                section=_clean(candidate.get("section"), 300), limit=5,
            )
        except Exception:
            memory_hints = []
        current = mask_sensitive_data({
            "event": "target_selected",
            "at": utc_now(),
            "action_id": action_id,
            "phase": phase,
            "page_url": _clean(getattr(page, "url", ""), 700),
            "intent": _clean(intent or action, 500),
            "action": _clean(action, 100),
            "expected_value": _safe_value(label or intent, expected_value),
            "active_surface": surface_summary,
            "selected_control": {
                "semantic_control_id": str(resolution.get("semantic_control_id") or ""),
                "label": label,
                "section": _clean(candidate.get("section"), 300),
                "role": _clean(candidate.get("role"), 120),
                "control_type": _clean(candidate.get("type") or candidate.get("tag"), 120),
                "confidence": resolution.get("confidence"),
                "margin": resolution.get("margin"),
                "gate_status": resolution.get("status"),
                "anchored": candidate.get("anchor_match"),
                "owned_surface_visible": candidate.get("owned_surface_visible"),
            },
            "selection_evidence": {
                "source_scores": evidence.get("source_scores") or {},
                "playwright_mcp": {k: v for k, v in (evidence.get("playwright_mcp") or {}).items() if k != "text"},
                "devtools_mcp": {k: v for k, v in (evidence.get("devtools_mcp") or {}).items() if k != "text"},
                "hip_intelligence_mcp": evidence.get("hip_intelligence_mcp") or {},
                "vision": evidence.get("vision") or {},
                "reason_codes": list(((_candidate_rows(resolution)[0] if _candidate_rows(resolution) else {}).get("evidence_reasons") or []))[:8],
            },
            "candidate_ranking": _candidate_rows(resolution),
            "current_dropdown_options": current_options,
            "website_memory": {
                "status": "validated_prior_available" if any(h.get("trust") == "validated" for h in memory_hints if isinstance(h, Mapping)) else "candidate_or_new",
                "hints": memory_hints,
                "summary": self.world_model.summary(phase=phase),
                "live_reproof_required": True,
                "customer_values_stored": False,
                "selectors_stored": False,
                "coordinates_stored": False,
            },
            "planner": {},
            "execution": {"status": "pending"},
            "verification": {"status": "pending"},
            "visual_overlay": public_overlay_telemetry(overlay_runtime),
            "overlay_legend": overlay_legend(),
            "screenshot_relative_path": screenshot,
            "screenshot_stage": "before_action_with_selected_target",
            "selectors_stored": False,
            "coordinates_stored": False,
        })
        self._append(current)
        return current

    async def record_acting(self, *, page: Any, action_id: str) -> None:
        """Expose the final governed pre-dispatch target as orange ACTING state."""
        if not self.enabled:
            return
        state = self._read()
        current = dict(state.get("current") or {})
        if action_id and str(current.get("action_id") or "") != str(action_id):
            return
        runtime = self._visual_runtime.get(str(action_id or ""), {})
        locator = runtime.get("locator")
        label = str(runtime.get("label") or current.get("intent") or "")
        try:
            overlay = await self.visual_overlay.render_acting(page=page, locator=locator, label=label)
        except Exception:
            overlay = {}
        current["event"] = "action_acting"
        current["at"] = utc_now()
        current["execution"] = {**(current.get("execution") or {}), "status": "acting"}
        current["visual_overlay"] = public_overlay_telemetry(overlay)
        shot = await self._screenshot(page=page, locator=locator, action_id=action_id, label=label, suffix="acting")
        if shot:
            current["screenshot_relative_path"] = shot
            current["screenshot_stage"] = "pre_dispatch_acting"
        self._append(current)

    def record_planner(self, *, action_id: str, decision: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        state = self._read()
        current = dict(state.get("current") or {})
        if action_id and str(current.get("action_id") or "") != action_id:
            return
        current["event"] = "planner_aligned"
        current["at"] = utc_now()
        current["planner"] = mask_sensitive_data({
            "framework": decision.get("framework") or "autowebglm",
            "status": decision.get("status") or "",
            "aligned": decision.get("aligned"),
            "reason": _clean(decision.get("reason"), 700),
            "tool_adapter_required": decision.get("tool_adapter_required"),
        })
        self._append(current)

    async def record_result(self, *, page: Any, event: Any) -> None:
        if not self.enabled:
            return
        raw = getattr(event, "__dict__", None) or {}
        action_id = str(raw.get("action_id") or "")
        state = self._read()
        current = dict(state.get("current") or {})
        if action_id and str(current.get("action_id") or "") != action_id:
            current = {
                "event": "action_result",
                "at": utc_now(),
                "action_id": action_id,
                "phase": "",
                "intent": _clean(raw.get("target"), 500),
                "candidate_ranking": [],
                "selected_control": {},
            }
        execution = raw.get("execution_provenance") if isinstance(raw.get("execution_provenance"), Mapping) else {}
        verified_effect = bool(raw.get("success", True) and execution.get("semantic_effect_pass") is not False)
        runtime = self._visual_runtime.get(action_id, {})
        # Navigation/session actions do not pass through the semantic target gate and
        # therefore have no visual target runtime. Do not inject an overlay for them.
        if runtime:
            try:
                overlay = await self.visual_overlay.render_result(
                    page=page, locator=runtime.get("locator"),
                    label=str(runtime.get("label") or _clean(raw.get("target"), 180)),
                    success=verified_effect, previous_seen_keys=runtime.get("seen_keys") or [],
                )
            except Exception:
                overlay = {}
        else:
            overlay = {}
        screenshot = await self._screenshot(
            page=page,
            locator=runtime.get("locator"),
            action_id=action_id,
            label=str(runtime.get("label") or _clean(raw.get("target"), 180)),
            suffix="verified" if verified_effect else "failed",
        )
        current["event"] = "action_verified" if verified_effect else "action_failed"
        current["at"] = utc_now()
        current["execution"] = mask_sensitive_data({
            "status": "completed" if verified_effect else "failed",
            "primary_executor": execution.get("primary_executor") or "",
            "actual_executor": execution.get("actual_executor") or raw.get("backend") or "",
            "fallback_reason": _clean(execution.get("fallback_reason") or raw.get("error"), 900),
            "playwright_mcp_attempted": bool(execution.get("playwright_mcp_attempted")),
            "playwright_mcp_succeeded": bool(execution.get("playwright_mcp_succeeded")),
            "semantic_revalidation": execution.get("semantic_revalidation") or "",
        })
        current["verification"] = mask_sensitive_data({
            "status": "verified" if verified_effect else "failed",
            "semantic_effect_pass": execution.get("semantic_effect_pass"),
            "semantic_effect_type": execution.get("semantic_effect_type") or "",
            "semantic_effect_confidence": execution.get("semantic_effect_confidence"),
            "exact_value_commit_verified": execution.get("exact_value_commit_verified"),
            "observed_value": _safe_value(str(raw.get("target") or ""), execution.get("observed_value")),
            "error": _clean(raw.get("error"), 900),
        })
        current["visual_overlay"] = public_overlay_telemetry(overlay)
        current["overlay_legend"] = overlay_legend()
        if screenshot:
            current["screenshot_relative_path"] = screenshot
            current["screenshot_stage"] = "after_action_verified" if verified_effect else "after_action_failed"
        current["page_url_after"] = _clean(raw.get("page_url_after"), 700)
        try:
            phase = str(current.get("phase") or "")
            memory = dict(current.get("website_memory") or {})
            verified = bool(current.get("verification", {}).get("status") == "verified")
            if verified and current.get("current_dropdown_options") and current.get("expected_value") not in (None, ""):
                selected = current.get("selected_control") if isinstance(current.get("selected_control"), Mapping) else {}
                choice_update = self.world_model.record_verified_portal_choice(
                    phase=phase, action=str(current.get("action") or "select"),
                    control=selected, choice=str(current.get("expected_value") or ""),
                    available_options=list(current.get("current_dropdown_options") or []),
                    effect_type=str(current.get("verification", {}).get("semantic_effect_type") or ""),
                    effect_confidence=float(current.get("verification", {}).get("semantic_effect_confidence") or 0.0),
                )
                memory["portal_choice_update"] = choice_update
            memory["summary"] = self.world_model.summary(phase=phase)
            memory["last_action_verified"] = verified
            current["website_memory"] = memory
        except Exception:
            pass
        self._append(current)
        self._visual_runtime.pop(action_id, None)


def read_agent_live_view(run_dir: str | Path) -> Dict[str, Any]:
    path = Path(run_dir) / LIVE_VIEW_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
