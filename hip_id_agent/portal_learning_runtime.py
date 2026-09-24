from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .hip_form_catalog import classify_form_surface, catalog_manifest
from .website_understanding import WebsiteUnderstandingEngine


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _event_dict(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {}


def _type_shape(value: Any, depth: int = 0) -> Any:
    """Return a compact value-free JSON shape for API learning."""
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _type_shape(v, depth + 1) for k, v in sorted(value.items(), key=lambda x: str(x[0]))[:120]}
    if isinstance(value, list):
        return [_type_shape(value[0], depth + 1)] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _try_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text[0:1] not in {"{", "["}:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _endpoint_template(url: str) -> str:
    """Normalize volatile IDs while preserving the portal API route contract."""
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return str(url or "").split("?", 1)[0]
    parts: List[str] = []
    for raw in (parsed.path or "/").split("/"):
        part = raw.strip()
        if not part:
            continue
        if re.fullmatch(r"\d+", part):
            part = "{id}"
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", part, re.I):
            part = "{uuid}"
        elif re.fullmatch(r"[0-9a-f]{16,}", part, re.I):
            part = "{token}"
        parts.append(part)
    host = (parsed.hostname or "").lower()
    return f"{parsed.scheme or 'https'}://{host}/" + "/".join(parts)


def _safe_action_text(value: Any) -> bool:
    text = _norm_text(value)
    unsafe = (
        "save", "submit", "create", "delete", "deploy", "publish", "update",
        "confirm", "remove", "enable", "disable", "archive", "reset",
    )
    return bool(text) and not any(token in text for token in unsafe)


class PortalLearningRuntime:
    """Deep, non-blocking learning layer for the HIP Portal.

    The runtime does not perform final mutations. It observes the same persistent
    Chrome context through Python Playwright, Playwright MCP and Chrome DevTools MCP,
    builds compact causal/API/validation models, and promotes knowledge only after
    the phase's independent judge passes.
    """

    SCHEMA_VERSION = "hip.portal-learning.v1"

    def __init__(
        self,
        *,
        config: Any,
        root_dir: str | Path,
        browser: Any,
        brain: Any = None,
        run_id: str = "",
    ) -> None:
        self.config = config
        self.policy = getattr(config, "portal_learning", None)
        self.enabled = bool(getattr(self.policy, "enabled", True))
        self.root_dir = Path(root_dir)
        self.browser = browser
        self.brain = brain
        self.run_id = str(run_id or self.root_dir.name)
        self.states: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self.manifest_path = self.root_dir / "portal_learning" / "portal_learning_manifest.json"
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.website_understanding = WebsiteUnderstandingEngine(config=config, browser=browser)
        self._write_manifest()

    def _write_manifest(self) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "enabled": self.enabled,
            "updated_at": utc_now(),
            "attempts": [
                {
                    "phase": state.get("phase"),
                    "attempt": state.get("attempt"),
                    "status": state.get("status", "started"),
                    "summary": state.get("summary_path", ""),
                }
                for state in self.states.values()
            ],
            "learning_channels": [
                "python_playwright_dom",
                "playwright_mcp_accessibility",
                "chrome_devtools_mcp_dom_network_console",
                "api_contract_shapes",
                "form_open_api_delta",
                "ui_fill_api_delta",
                "input_ui_api_crosswalk",
                "blocked_submit_payload_capture",
                "observed_openapi_postman",
                "validation_rules",
                "action_state_transitions",
                "coverage_and_drift",
                "optional_performance_trace",
                "universal_form_family_classification",
                "judge_gated_same_flow_pattern_memory",
                "deep_dom_layout_event_option_understanding",
            ],
            "form_family_catalog": catalog_manifest(),
            "safety": {
                "storage_values_captured": False,
                "authorization_values_captured": False,
                "final_mutations_allowed": False,
                "submit_payload_capture_network_aborted": True,
                "api_write_requires_two_key_confirmation": True,
                "promotion_requires_judge_pass": bool(getattr(self.policy, "promote_only_after_judge_pass", True)),
            },
        }
        safe_write_json(self.manifest_path, payload)

    def capability_matrix(self) -> Dict[str, Any]:
        pw = getattr(self.browser, "playwright_mcp_backend", None)
        cdp = getattr(self.browser, "mcp_backend", None)
        pw_tools = sorted(getattr(getattr(pw, "client", None), "tools", {}) or {})
        cdp_tools = sorted(getattr(getattr(cdp, "client", None), "tools", {}) or {})
        matrix = {
            "schema_version": "hip.portal-learning-mcp-capabilities.v1",
            "playwright_mcp": {
                "available": bool(pw),
                "tool_count": len(pw_tools),
                "tools": pw_tools,
                "learning_support": {
                    "accessibility_snapshot": "browser_snapshot" in pw_tools,
                    "network": "browser_network_requests" in pw_tools,
                    "console": "browser_console_messages" in pw_tools,
                    "evaluate": "browser_evaluate" in pw_tools,
                    "storage": any("storage" in name for name in pw_tools),
                    "tabs": any("tab" in name for name in pw_tools),
                },
            },
            "chrome_devtools_mcp": {
                "available": bool(cdp),
                "tool_count": len(cdp_tools),
                "tools": cdp_tools,
                "learning_support": {
                    "dom_snapshot": "take_snapshot" in cdp_tools,
                    "network": "list_network_requests" in cdp_tools,
                    "console": "list_console_messages" in cdp_tools,
                    "evaluate": "evaluate_script" in cdp_tools,
                    "performance_trace": "performance_start_trace" in cdp_tools and "performance_stop_trace" in cdp_tools,
                    "performance_insight": "performance_analyze_insight" in cdp_tools,
                },
            },
            "recommendation": "Use both attached to the same Microsoft Edge context; do not launch a third browser controller.",
        }
        safe_write_json(self.root_dir / "portal_learning" / "mcp_learning_capability_matrix.json", matrix)
        return matrix

    async def begin_attempt(
        self,
        *,
        phase: str,
        attempt: int,
        phase_dir: str | Path,
        contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        phase_dir = Path(phase_dir)
        out_dir = phase_dir / "portal_learning" / f"attempt_{attempt:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "phase": phase,
            "attempt": int(attempt),
            "phase_dir": str(phase_dir),
            "out_dir": str(out_dir),
            "contract": mask_sensitive_data(contract or {}),
            "started_at": utc_now(),
            "action_start": len(getattr(self.browser, "action_events", []) or []),
            "network_start": len(getattr(self.browser, "network_tab_events", []) or []),
            "transition_start": len(getattr(self.browser, "dom_transition_records", []) or []),
            "status": "started",
            "performance_trace_started": False,
        }
        self.states[(phase, int(attempt))] = state
        self.capability_matrix()
        before = await self._capture_observation(phase=phase, label="before", out_dir=out_dir)
        state["before"] = before
        safe_write_json(out_dir / "before_observation.json", before)

        retry_only = bool(getattr(self.policy, "performance_trace_on_retry_only", True))
        allow_trace = bool(getattr(self.policy, "capture_performance_trace", True)) and (not retry_only or int(attempt) > 1)
        if allow_trace:
            cdp = getattr(self.browser, "mcp_backend", None)
            if cdp is not None and hasattr(cdp, "start_performance_trace"):
                try:
                    trace = await cdp.start_performance_trace(reload=False, auto_stop=False)
                    state["performance_trace_started"] = bool(trace.get("started"))
                    safe_write_json(out_dir / "performance_trace_start.json", trace)
                except Exception as exc:
                    safe_write_json(out_dir / "performance_trace_start.json", {"started": False, "error": mask_sensitive_string(str(exc))})
        self._write_manifest()
        return {"status": "started", "observation": str(out_dir / "before_observation.json")}

    async def finish_attempt(
        self,
        *,
        phase: str,
        attempt: int,
        verification: Optional[Dict[str, Any]] = None,
        judge_result: Optional[Dict[str, Any]] = None,
        success: bool,
        error: str = "",
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        state = self.states.get((phase, int(attempt)))
        if state is None:
            # Keep the learning layer non-blocking for callers that start midway.
            phase_dir = self.root_dir / phase
            await self.begin_attempt(phase=phase, attempt=attempt, phase_dir=phase_dir, contract={})
            state = self.states[(phase, int(attempt))]
        out_dir = Path(state["out_dir"])

        performance: Dict[str, Any] = {"captured": False}
        if state.get("performance_trace_started"):
            cdp = getattr(self.browser, "mcp_backend", None)
            if cdp is not None and hasattr(cdp, "stop_performance_trace"):
                try:
                    performance = await cdp.stop_performance_trace()
                except Exception as exc:
                    performance = {"captured": False, "error": mask_sensitive_string(str(exc))}
                safe_write_json(out_dir / "performance_trace_stop.json", performance)

        after = await self._capture_observation(phase=phase, label="after", out_dir=out_dir)
        safe_write_json(out_dir / "after_observation.json", after)
        summary = self._build_summary(
            state=state,
            before=state.get("before") or {},
            after=after,
            verification=verification or {},
            judge_result=judge_result or {},
            success=bool(success),
            error=error,
            performance=performance,
        )
        summary_path = out_dir / "portal_learning_summary.json"
        safe_write_json(summary_path, summary)
        state["status"] = summary.get("trust", "negative")
        state["summary_path"] = str(summary_path)
        state["finished_at"] = utc_now()

        if self.brain is not None and hasattr(self.brain, "ingest_autonomous_learning"):
            try:
                promotion = self.brain.ingest_autonomous_learning(
                    phase=phase,
                    summary=summary,
                    trust=str(summary.get("trust") or "negative"),
                    run_id=self.run_id,
                )
            except Exception as exc:
                promotion = {"status": "error", "error": mask_sensitive_string(str(exc))}
            summary["portal_brain_promotion"] = promotion
            safe_write_json(summary_path, summary)

        self._write_manifest()
        return summary

    async def _capture_observation(self, *, phase: str, label: str, out_dir: Path) -> Dict[str, Any]:
        observation: Dict[str, Any] = {
            "schema_version": "hip.portal-learning-observation.v1",
            "phase": phase,
            "label": label,
            "captured_at": utc_now(),
            "local_dom": {},
            "playwright_mcp": {},
            "chrome_devtools_mcp": {},
            "website_understanding": {},
        }
        page = getattr(self.browser, "page", None)
        if page is not None:
            try:
                observation["local_dom"] = mask_sensitive_data(await page.evaluate(
                    """() => {
                      const text = el => String(el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
                      const visible = el => {
                        if (!el || !el.isConnected) return false;
                        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                        return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
                      };
                      const labelFor = el => {
                        const aria = el.getAttribute('aria-label'); if (aria) return aria.trim();
                        const by = el.getAttribute('aria-labelledby');
                        if (by) { const t = by.split(/\\s+/).map(id => text(document.getElementById(id))).filter(Boolean).join(' '); if (t) return t; }
                        if (el.id) { const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (lab && text(lab)) return text(lab); }
                        const wrapped = el.closest('label'); if (wrapped && text(wrapped)) return text(wrapped);
                        const field = el.closest('dds-form-field, .dds__form-group, .form-group, fieldset, [role="group"]');
                        if (field) { const lab = field.querySelector('label, legend, .dds__label, [class*="label"]'); if (lab && text(lab)) return text(lab); }
                        return '';
                      };
                      const nodes = [...document.querySelectorAll('input, select, textarea, button, [role="combobox"], [role="checkbox"], [role="radio"], [role="tab"], [contenteditable="true"], dds-dropdown, dds-input, dds-checkbox, dds-radio-button')];
                      const controls = nodes.filter(visible).slice(0, 1200).map((el, index) => {
                        const tag = el.tagName.toLowerCase(); const role = el.getAttribute('role') || '';
                        const rect = el.getBoundingClientRect();
                        const selected = [...el.querySelectorAll?.('[aria-selected="true"], [aria-checked="true"], option:checked, .dds__tag') || []].map(text).filter(Boolean).slice(0, 60);
                        return {
                          index, tag, role, type: el.getAttribute('type') || '', name: el.getAttribute('name') || '', id: el.id || '',
                          label: labelFor(el), ariaLabel: el.getAttribute('aria-label') || '', placeholder: el.getAttribute('placeholder') || '',
                          required: !!(el.required || el.getAttribute('aria-required') === 'true'), disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                          invalid: el.getAttribute('aria-invalid') === 'true', multiple: !!(el.multiple || el.getAttribute('aria-multiselectable') === 'true'),
                          hasValue: String(el.value || '').trim().length > 0, selectedLabels: selected,
                          validation: {min: el.getAttribute('min'), max: el.getAttribute('max'), minLength: el.getAttribute('minlength'), maxLength: el.getAttribute('maxlength'), pattern: el.getAttribute('pattern')},
                          box: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)}
                        };
                      });
                      const alerts = [...document.querySelectorAll('[role="alert"], [aria-live], .error, .invalid-feedback, [class*="error"], [class*="validation"]')]
                        .filter(visible).map(text).filter(Boolean).slice(0, 120);
                      const headings = [...document.querySelectorAll('h1,h2,h3,h4,[role="heading"]')].filter(visible).map(text).filter(Boolean).slice(0, 80);
                      const tabs = [...document.querySelectorAll('[role="tab"], dds-tabs button, .dds__tabs button')].filter(visible).map(el => ({text:text(el), selected:el.getAttribute('aria-selected') === 'true'})).slice(0, 80);
                      const surfaces = [...document.querySelectorAll('form, [role="dialog"], [role="drawer"], dds-drawer, .dds__drawer, fieldset')].filter(visible).map(el => ({tag:el.tagName.toLowerCase(), role:el.getAttribute('role')||'', text:text(el).slice(0,500)})).slice(0,80);
                      return {
                        url: location.href, title: document.title, readyState: document.readyState,
                        headings, tabs, surfaces, controls, alerts,
                        storageKeyNames: {localStorage:Object.keys(localStorage).sort(), sessionStorage:Object.keys(sessionStorage).sort()},
                        storageValuesCaptured: false,
                        bodyFingerprintSource: text(document.body).slice(0, 12000)
                      };
                    }"""
                ))
            except Exception as exc:
                observation["local_dom"] = {"error": mask_sensitive_string(str(exc))}

        try:
            observation["website_understanding"] = await self.website_understanding.capture(
                page=page, phase=phase, stage=label, output_dir=out_dir / f"{label}_website_understanding"
            ) if page is not None else {"available": False, "reason": "page_unavailable"}
        except Exception as exc:
            observation["website_understanding"] = {
                "available": False, "reason": "capture_exception",
                "error": mask_sensitive_string(str(exc))[:1200],
            }

        pw = getattr(self.browser, "playwright_mcp_backend", None)
        if pw is not None and bool(getattr(self.policy, "capture_accessibility_snapshots", True)):
            try:
                snap = await pw.snapshot(boxes=True, depth=int(getattr(self.policy, "accessibility_snapshot_depth", 12)))
                text = str(snap.get("text") or "")
                observation["playwright_mcp"] = {"available": True, "snapshot_text": text[:120000], "fingerprint": _hash(text)}
            except Exception as exc:
                observation["playwright_mcp"] = {"available": True, "error": mask_sensitive_string(str(exc))}

        cdp = getattr(self.browser, "mcp_backend", None)
        if cdp is not None and bool(getattr(self.policy, "capture_devtools_snapshots", True)):
            try:
                snap = await cdp.get_dom_snapshot()
                text = str(snap.get("snapshot_text") or "")
                observation["chrome_devtools_mcp"] = {"available": True, "snapshot_text": text[:120000], "fingerprint": _hash(text)}
            except Exception as exc:
                observation["chrome_devtools_mcp"] = {"available": True, "error": mask_sensitive_string(str(exc))}

        local = observation.get("local_dom") or {}
        controls = local.get("controls") if isinstance(local, dict) else []
        visible_text = " ".join([
            *(local.get("headings") or [] if isinstance(local, dict) else []),
            *[str((c or {}).get("label") or (c or {}).get("ariaLabel") or "") for c in (controls or []) if isinstance(c, dict)],
        ])
        form_classification = classify_form_surface(
            url=str(local.get("url") or "") if isinstance(local, dict) else "",
            text=visible_text,
            controls=controls or [],
        )
        observation["form_surface_classification"] = form_classification
        compact = {
            "url": local.get("url") if isinstance(local, dict) else "",
            "title": local.get("title") if isinstance(local, dict) else "",
            "headings": local.get("headings") if isinstance(local, dict) else [],
            "control_count": len(controls or []),
            "required_control_count": sum(1 for c in controls or [] if isinstance(c, dict) and c.get("required")),
            "invalid_control_count": sum(1 for c in controls or [] if isinstance(c, dict) and c.get("invalid")),
            "alerts": local.get("alerts") if isinstance(local, dict) else [],
            "form_surface_classification": form_classification,
            "website_understanding_gate": ((observation.get("website_understanding") or {}).get("understanding_gate") or {}),
            "active_surface": ((observation.get("website_understanding") or {}).get("active_surface") or {}),
            "option_catalog_count": len(((observation.get("website_understanding") or {}).get("option_catalog") or {})),
        }
        safe_write_json(out_dir / f"{label}_compact_page_model.json", compact)
        return observation

    def _control_signature(self, control: Dict[str, Any]) -> Dict[str, Any]:
        signature = {
            "semantic": _norm(control.get("label") or control.get("ariaLabel") or control.get("placeholder") or control.get("name")),
            "tag": control.get("tag"),
            "role": control.get("role"),
            "type": control.get("type"),
            "required": bool(control.get("required")),
            "multiple": bool(control.get("multiple")),
            "validation": control.get("validation") or {},
        }
        signature["control_id"] = _hash(signature)
        return signature

    def _api_contracts(self, events: Iterable[Any]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        limit = int(getattr(self.policy, "max_network_contracts_per_phase", 250))
        for raw in events:
            event = _event_dict(raw)
            url = str(event.get("url") or "")
            if not url.lower().startswith(("http://", "https://")):
                continue
            method = str(event.get("method") or "GET").upper()
            template = _endpoint_template(url)
            key = f"{method} {template}"
            row = grouped.setdefault(key, {
                "contract_id": _hash(key), "method": method, "endpoint_template": template,
                "statuses": [], "resource_types": [], "query_keys": [], "request_shapes": [], "response_shapes": [], "stages": [], "sources": [],
            })
            if event.get("status") is not None:
                row["statuses"].append(event.get("status"))
            row["resource_types"].append(event.get("resource_type"))
            row["stages"].append(event.get("stage"))
            row["sources"].append(event.get("source"))
            try:
                row["query_keys"].extend(k for k, _ in parse_qsl(urlparse(url).query, keep_blank_values=True))
            except Exception:
                pass
            request = _try_json(event.get("request_body_redacted"))
            response = event.get("response_body_redacted")
            if response is None:
                response = _try_json(event.get("response_body_text_redacted"))
            if request is not None:
                row["request_shapes"].append(_type_shape(request))
            if response is not None:
                row["response_shapes"].append(_type_shape(response))
            if len(grouped) >= limit:
                break
        for row in grouped.values():
            for name in ("statuses", "resource_types", "query_keys", "request_shapes", "response_shapes", "stages", "sources"):
                unique: List[Any] = []
                seen: set[str] = set()
                for value in row[name]:
                    token = json.dumps(value, sort_keys=True, default=str)
                    if token not in seen:
                        seen.add(token); unique.append(value)
                row[name] = unique[:40]
        return sorted(grouped.values(), key=lambda x: (x["endpoint_template"], x["method"]))

    def _build_summary(
        self,
        *,
        state: Dict[str, Any],
        before: Dict[str, Any],
        after: Dict[str, Any],
        verification: Dict[str, Any],
        judge_result: Dict[str, Any],
        success: bool,
        error: str,
        performance: Dict[str, Any],
    ) -> Dict[str, Any]:
        phase = str(state.get("phase") or "")
        contract = state.get("contract") or {}
        before_local = before.get("local_dom") if isinstance(before, dict) else {}
        after_local = after.get("local_dom") if isinstance(after, dict) else {}
        before_controls = before_local.get("controls") if isinstance(before_local, dict) else []
        after_controls = after_local.get("controls") if isinstance(after_local, dict) else []
        before_sigs = {self._control_signature(c)["control_id"]: self._control_signature(c) for c in before_controls or [] if isinstance(c, dict)}
        after_sigs = {self._control_signature(c)["control_id"]: self._control_signature(c) for c in after_controls or [] if isinstance(c, dict)}

        action_slice = list(getattr(self.browser, "action_events", []) or [])[int(state.get("action_start", 0)):]
        network_slice = list(getattr(self.browser, "network_tab_events", []) or [])[int(state.get("network_start", 0)):]
        transition_slice = list(getattr(self.browser, "dom_transition_records", []) or [])[int(state.get("transition_start", 0)):]
        actions = [_event_dict(x) for x in action_slice]
        navigation_edges: List[Dict[str, Any]] = []
        action_api_links: List[Dict[str, Any]] = []
        network_by_id = {_event_dict(x).get("request_id"): _event_dict(x) for x in network_slice}
        for action in actions:
            before_url = str(action.get("page_url_before") or "")
            after_url = str(action.get("page_url_after") or "")
            if before_url and after_url and before_url != after_url:
                navigation_edges.append({
                    "from": _endpoint_template(before_url), "to": _endpoint_template(after_url),
                    "action": action.get("type"), "target": action.get("target"), "success": action.get("success"),
                })
            linked = []
            for request_id in action.get("network_events_triggered") or []:
                event = network_by_id.get(request_id)
                if event and str(event.get("url") or "").startswith(("http://", "https://")):
                    linked.append({"method": event.get("method"), "endpoint_template": _endpoint_template(str(event.get("url")))})
            if linked:
                action_api_links.append({"action": action.get("type"), "target": action.get("target"), "api_contracts": linked})

        validation_rules = []
        for control in after_controls or []:
            if not isinstance(control, dict):
                continue
            sig = self._control_signature(control)
            validation = control.get("validation") or {}
            if control.get("required") or control.get("invalid") or any(v not in {None, ""} for v in validation.values()):
                validation_rules.append({
                    "control_id": sig["control_id"], "semantic": sig["semantic"], "required": bool(control.get("required")),
                    "invalid": bool(control.get("invalid")), "constraints": validation,
                })

        markers = [str(x) for x in contract.get("surface_markers") or []]
        combined_text = " ".join([
            " ".join(str(x) for x in (after_local.get("headings") or [])) if isinstance(after_local, dict) else "",
            str((after.get("playwright_mcp") or {}).get("snapshot_text") or "")[:40000],
            str((after.get("chrome_devtools_mcp") or {}).get("snapshot_text") or "")[:40000],
        ]).lower()
        marker_coverage = {marker: _norm_text(marker) in _norm_text(combined_text) for marker in markers}
        judge_pass = bool(judge_result.get("pass", True))
        deterministic_pass = str(verification.get("status") or "").lower() not in {"failed", "fail", "blocked"}
        promote_requires_judge = bool(getattr(self.policy, "promote_only_after_judge_pass", True))
        trusted_success = bool(success and deterministic_pass and (judge_pass or not promote_requires_judge))
        trust = "validated" if trusted_success else "candidate" if success else "negative"

        page_fingerprint_payload = {
            "url": _endpoint_template(str(after_local.get("url") or "")) if isinstance(after_local, dict) else "",
            "headings": after_local.get("headings") if isinstance(after_local, dict) else [],
            "tabs": after_local.get("tabs") if isinstance(after_local, dict) else [],
            "controls": sorted(after_sigs),
        }
        page_fingerprint = _hash(page_fingerprint_payload)
        unexplored = []
        used_targets = _norm_text(" ".join(str(a.get("target") or "") for a in actions))
        for control in after_controls or []:
            if not isinstance(control, dict) or control.get("disabled"):
                continue
            semantic = control.get("label") or control.get("ariaLabel") or control.get("placeholder") or control.get("name")
            if not _safe_action_text(semantic):
                continue
            if _norm_text(semantic) and _norm_text(semantic) not in used_targets:
                unexplored.append({"semantic": semantic, "tag": control.get("tag"), "role": control.get("role"), "safe_candidate": True})
        unexplored = unexplored[: int(getattr(self.policy, "max_exploration_agenda_items", 50))]

        return mask_sensitive_data({
            "schema_version": self.SCHEMA_VERSION,
            "phase": phase,
            "attempt": state.get("attempt"),
            "run_id": self.run_id,
            "trust": trust,
            "success": bool(success),
            "deterministic_pass": deterministic_pass,
            "judge_pass": judge_pass,
            "error": mask_sensitive_string(error) if error else "",
            "page_model": {
                "fingerprint": page_fingerprint,
                "url_template": page_fingerprint_payload["url"],
                "headings": page_fingerprint_payload["headings"],
                "tabs": page_fingerprint_payload["tabs"],
                "surface_marker_coverage": marker_coverage,
                "control_count_before": len(before_sigs),
                "control_count_after": len(after_sigs),
                "controls_added": [after_sigs[k] for k in sorted(set(after_sigs) - set(before_sigs))],
                "controls_removed": [before_sigs[k] for k in sorted(set(before_sigs) - set(after_sigs))],
                "control_signatures": list(after_sigs.values())[: int(getattr(self.policy, "max_control_signatures_per_phase", 800))],
            },
            "validation_rules": validation_rules,
            "api_contracts": self._api_contracts(network_slice),
            "navigation_edges": navigation_edges[:200],
            "action_api_links": action_api_links[:300],
            "dom_state_transitions": [mask_sensitive_data(_event_dict(x)) for x in transition_slice[-300:]],
            "console_signatures": self._console_signatures(),
            "storage_key_names": (after_local.get("storageKeyNames") if isinstance(after_local, dict) else {}) or {},
            "storage_values_captured": False,
            "performance": performance,
            "coverage": {
                "surface_markers": marker_coverage,
                "exact_state_checks": contract.get("exact_state_checks") or [],
                "repeatable_kinds": contract.get("repeatable_kinds") or [],
                "unexplored_safe_controls": unexplored,
                "learning_channel_status": {
                    "local_dom": not bool((after_local or {}).get("error")) if isinstance(after_local, dict) else False,
                    "playwright_mcp": bool((after.get("playwright_mcp") or {}).get("available")),
                    "chrome_devtools_mcp": bool((after.get("chrome_devtools_mcp") or {}).get("available")),
                    "network_contract_count": len(self._api_contracts(network_slice)),
                },
            },
            "drift": {
                "before_fingerprint": _hash({"controls": sorted(before_sigs), "url": before_local.get("url") if isinstance(before_local, dict) else ""}),
                "after_fingerprint": page_fingerprint,
                "changed": bool(set(before_sigs) != set(after_sigs)),
            },
            "mcp_capabilities": self.capability_matrix(),
            "promotion_policy": {
                "promote_only_after_judge_pass": promote_requires_judge,
                "candidate_on_incomplete_or_failed_judge": True,
            },
            "captured_at": utc_now(),
        })

    def _console_signatures(self) -> List[Dict[str, Any]]:
        rows = []
        seen = set()
        for raw in list(getattr(self.browser, "console_messages", []) or [])[-300:]:
            row = _event_dict(raw)
            text = mask_sensitive_string(str(row.get("text") or row.get("message") or row))
            compact = re.sub(r"\b\d+\b", "{n}", text)[:1000]
            key = _hash(compact)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"signature": key, "type": row.get("type") or row.get("level") or "console", "message_template": compact})
        return rows[:120]
