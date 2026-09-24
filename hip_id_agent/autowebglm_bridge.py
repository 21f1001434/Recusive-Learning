from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .aia_client import AIAClient
from .model_portfolio import model_portfolio_from_config
from .security import mask_sensitive_data, mask_sensitive_string


_ACTION_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*$", re.S)
_MUTATION_WORDS = {
    "save", "create", "delete", "deploy", "submit", "publish", "update",
    "confirm", "migrate", "clone", "remove", "approve", "reject",
}


@dataclass
class AutoWebGLMStatus:
    enabled: bool
    protocol: str = "AutoWebGLM task+simplified-html+position+history -> single action"
    upstream: str = "https://github.com/THUDM/AutoWebGLM"
    native_model_configured: bool = False
    dell_aia_protocol_planner: bool = True
    recovery_only: bool = False
    primary_framework: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "protocol": self.protocol,
            "upstream": self.upstream,
            "native_model_configured": self.native_model_configured,
            "dell_aia_protocol_planner": self.dell_aia_protocol_planner,
            "recovery_only": self.recovery_only,
            "primary_framework": self.primary_framework,
            "framework_role": "primary_browser_decision_layer" if self.primary_framework else "auxiliary",
            "deterministic_drivers_role": "verified_tool_adapters",
            "runtime_compatibility": {
                "task_description": True,
                "html_simplification": True,
                "viewport_position": True,
                "previous_operations": True,
                "official_ten_action_protocol": True,
                "human_ai_expertise": "HIP reviewed KB + operator feedback/validated trajectory memory",
                "reinforcement_learning_runtime_equivalent": "AgentQ online transition rewards; no checkpoint weight training in production runtime",
                "rejection_sampling_runtime_equivalent": "unsafe/negative-reward proposals are rejected before execution",
                "native_chatglm3_6b_optional": True,
                "research_training_and_benchmark_assets_bundled": False,
            },
        }


class AutoWebGLMRecoveryBridge:
    """AutoWebGLM-compatible perception/planning on the existing HIP browser.

    AutoWebGLM is a research web-navigation agent built around a compact observation
    consisting of the task, simplified HTML, current page position and previous
    operations, followed by one browser action.  HIP reuses that protocol as a
    bounded recovery planner.  It never owns Chrome and it never bypasses AgentQ,
    deterministic field mapping, post-action verification or mutation governance.

    The original 6B checkpoint is intentionally not bundled.  If ``native_model_command``
    is configured, that external wrapper may return one AutoWebGLM command. Otherwise
    the existing Dell AIA model predicts the same protocol, which keeps deployment
    practical in the user's existing enterprise environment.
    """

    def __init__(self, config: Any, *, aia_config: Any = None, app_config: Any = None):
        self.config = config
        self.enabled = bool(getattr(config, "enabled", True))
        self.primary_framework = bool(getattr(config, "primary_framework", True))
        self.recovery_only = bool(getattr(config, "recovery_only", False))
        self.deterministic_tool_fallback = bool(getattr(config, "deterministic_tool_fallback", True))
        self.require_intent_alignment = bool(getattr(config, "require_intent_alignment", True))
        self.max_primary_decision_seconds = max(3, int(getattr(config, "max_primary_decision_seconds", 12) or 12))
        self.allowed_actions = {
            str(x).strip() for x in list(getattr(config, "allowed_actions", []) or []) if str(x).strip()
        }
        self.native_model_command = list(getattr(config, "native_model_command", []) or [])
        self.use_existing_dell_aia = bool(getattr(config, "use_existing_dell_aia", True))
        self.max_html_chars = max(4000, int(getattr(config, "max_html_chars", 70000) or 70000))
        self.max_history_actions = max(1, int(getattr(config, "max_history_actions", 40) or 40))
        self.max_tabs = max(1, int(getattr(config, "max_tabs", 12) or 12))
        self.max_prompt_chars = max(8000, int(getattr(config, "max_prompt_chars", 110000) or 110000))
        self.timeout_seconds = max(3, int(getattr(config, "timeout_seconds", 20) or 20))
        self.require_agentq_reward_gate = bool(getattr(config, "require_agentq_reward_gate", True))
        self.block_mutation_labels = bool(getattr(config, "block_mutation_labels", True))
        self.aia = AIAClient(aia_config) if aia_config is not None else None
        self.model_portfolio = None
        if app_config is not None:
            try:
                self.model_portfolio = model_portfolio_from_config(app_config)
            except Exception:
                self.model_portfolio = None

    def status(self) -> Dict[str, Any]:
        return AutoWebGLMStatus(
            enabled=self.enabled,
            native_model_configured=bool(self.native_model_command),
            dell_aia_protocol_planner=bool(self.use_existing_dell_aia),
            recovery_only=self.recovery_only,
            primary_framework=self.primary_framework,
        ).as_dict()

    @staticmethod
    def _history_entry(item: Any) -> str:
        if isinstance(item, Mapping):
            action = str(item.get("action") or item.get("action_type") or item.get("kind") or "action")
            target = str(item.get("label") or item.get("field") or item.get("selector") or "")
            outcome = str(item.get("status") or item.get("result") or item.get("success") or "")
            return f"{action}({target}) -> {outcome}"[:360]
        return str(item)[:360]

    async def build_observation(
        self,
        *,
        page: Any,
        task: str,
        history: Optional[Sequence[Any]] = None,
        expected_intent: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a value-free AutoWebGLM-style observation from the live page."""
        if not self.enabled or page is None:
            return {"available": False, "reason": "AutoWebGLM bridge disabled or page unavailable"}
        try:
            semantic = await page.evaluate(
                """() => {
                  const esc = (s) => String(s || '').replace(/[<>]/g, ' ');
                  const els = Array.from(document.querySelectorAll(
                    'a,button,input,textarea,select,[role=button],[role=link],[role=combobox],[role=checkbox],[role=radio],[role=option],[contenteditable=true]'
                  )).slice(0, 700);
                  const visible = (el) => {
                    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                  };
                  const label = (el) => {
                    const id = el.id;
                    const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    return (el.getAttribute('aria-label') || lab?.textContent || el.getAttribute('placeholder') || el.textContent || '').trim().replace(/\\s+/g,' ').slice(0,220);
                  };
                  const rows = [];
                  let n = 0;
                  for (const el of els) {
                    if (!visible(el)) continue;
                    n += 1;
                    try { el.setAttribute('data-hip-autowebglm-id', String(n)); } catch(e) {}
                    const role = el.getAttribute('role') || el.tagName.toLowerCase();
                    const icon = Array.from(el.querySelectorAll('svg,use,i,dds-icon,[class*=icon]')).map(x => [
                      x.getAttribute('name'), x.getAttribute('icon-name'), x.getAttribute('data-icon'),
                      x.getAttribute('href'), x.getAttribute('xlink:href'), x.getAttribute('class'),
                      x.getAttribute('aria-label'), x.getAttribute('title')
                    ].filter(Boolean).join(' ')).filter(Boolean).join(' ').replace(/\\s+/g,' ').slice(0,300);
                    const attrs = [];
                    const field = el.closest('fieldset,.dds__form__field,.dds__form-field,.dds__field,[role=row],tr');
                    const headingNode = el.closest('section,form,[role=dialog],app-generic-drawer,main')?.querySelector('h1,h2,h3,h4,legend,[role=heading]');
                    const sectionHeading = (headingNode?.textContent || '').trim().replace(/\\s+/g,' ').slice(0,180);
                    const ancestorTags = []; let a = el.parentElement;
                    while (a && a !== document.body && ancestorTags.length < 4) {
                      ancestorTags.push((a.tagName || '').toLowerCase() + (a.getAttribute('role') ? `[role=${a.getAttribute('role')}]` : '')); a = a.parentElement;
                    }
                    const siblingLabels = field ? Array.from(field.querySelectorAll('label,legend')).map(x => (x.textContent||'').trim().replace(/\\s+/g,' ')).filter(Boolean).slice(0,4) : [];
                    const fc = el.getAttribute('formcontrolname'); if (fc) attrs.push(`formcontrolname="${esc(fc)}"`);
                    const ariaExpanded = el.getAttribute('aria-expanded'); if (ariaExpanded !== null) attrs.push(`aria-expanded="${esc(ariaExpanded)}"`);
                    const ariaChecked = el.getAttribute('aria-checked'); if (ariaChecked !== null) attrs.push(`aria-checked="${esc(ariaChecked)}"`);
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') attrs.push('disabled="true"');
                    // Deliberately exclude value/text typed into inputs.  We need structure,
                    // not customer data, for recovery planning.
                    rows.push(`<e id="${n}" role="${esc(role)}" label="${esc(label(el))}" icon="${esc(icon)}" section="${esc(sectionHeading)}" ancestors="${esc(ancestorTags.join('>'))}" sibling_labels="${esc(siblingLabels.join('|'))}" ${attrs.join(' ')}></e>`);
                  }
                  return {
                    html: `<html>\\n${rows.join('\\n')}\\n</html>`,
                    position: {x: Math.round(scrollX), y: Math.round(scrollY), maxX: Math.max(0, document.documentElement.scrollWidth-innerWidth), maxY: Math.max(0, document.documentElement.scrollHeight-innerHeight), viewportW: innerWidth, viewportH: innerHeight},
                    title: document.title || '',
                    url: location.href,
                    elementCount: rows.length,
                  };
                }"""
            )
            tabs: List[Dict[str, Any]] = []
            context = getattr(page, "context", None)
            ctx = context if not callable(context) else context()
            for idx, p in enumerate(list(getattr(ctx, "pages", []) or [])[: self.max_tabs]):
                try:
                    tabs.append({"index": idx, "url": str(getattr(p, "url", "") or ""), "title": str(await p.title())[:240]})
                except Exception:
                    tabs.append({"index": idx, "url": str(getattr(p, "url", "") or ""), "title": ""})
            hist = [self._history_entry(x) for x in list(history or [])[-self.max_history_actions :]]
            html = str((semantic or {}).get("html") or "")[: self.max_html_chars]
            payload = {
                "available": True,
                "schema_version": "hip.autowebglm-observation.v1",
                "task": str(task or "")[:4000],
                "simplified_html": html,
                "current_position": (semantic or {}).get("position") or {},
                "url": str((semantic or {}).get("url") or getattr(page, "url", "") or ""),
                "title": str((semantic or {}).get("title") or "")[:300],
                "tabs": tabs,
                "previous_operations": hist,
                "expected_intent": mask_sensitive_data(dict(expected_intent or {})),
                "element_count": int((semantic or {}).get("elementCount") or 0),
                "action_space": [
                    "click(id)", "hover(id)", "select(id, option)",
                    "type_string(id, text, enter)", "press_key(id, key)", "scroll_page(direction)", "go(url)",
                    "jump_to(url)", "switch_tab(index)", "user_input(prompt)", "finish(result)",
                ],
                "html_simplification": {
                    "strategy": "actionable-elements plus bounded ancestor/section/sibling-label context",
                    "values_excluded": True,
                    "max_elements": 700,
                },
                "policy": "AutoWebGLM is the primary browser-decision framework; deterministic HIP/DDS routines are verified tool adapters and exact DOM verification remains authoritative",
            }
            return mask_sensitive_data(payload)
        except Exception as exc:
            return {"available": False, "error": mask_sensitive_string(str(exc))[:1000]}

    @staticmethod
    def parse_action(command: str) -> Dict[str, Any]:
        text = str(command or "").strip()
        m = _ACTION_RE.match(text)
        if not m:
            return {"valid": False, "raw": text, "reason": "not a single AutoWebGLM-style function call"}
        action = m.group(1)
        raw_args = m.group(2).strip()
        # Do not eval model output. Parse simple quoted/comma-separated args safely.
        args: List[str] = []
        current = ""
        quote = ""
        escape = False
        for ch in raw_args:
            if escape:
                current += ch; escape = False; continue
            if ch == "\\":
                escape = True; current += ch; continue
            if quote:
                current += ch
                if ch == quote: quote = ""
                continue
            if ch in {"'", '"'}:
                quote = ch; current += ch; continue
            if ch == ",":
                args.append(current.strip().strip("'\"")); current = ""; continue
            current += ch
        if current.strip() or raw_args == "":
            args.append(current.strip().strip("'\""))
        if args == [""]:
            args = []
        return {"valid": True, "action": action, "args": args, "raw": text}

    def _safe_action(self, parsed: Mapping[str, Any]) -> Tuple[bool, str]:
        action = str(parsed.get("action") or "")
        if action not in self.allowed_actions:
            return False, "action outside configured AutoWebGLM allow-list"
        if self.block_mutation_labels:
            joined = " ".join(str(x).lower() for x in list(parsed.get("args") or []))
            if any(re.search(rf"\b{re.escape(word)}\b", joined) for word in _MUTATION_WORDS):
                return False, "target appears to be a final mutation control"
        return True, "safe recovery candidate"


    @staticmethod
    def _semantic_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[_\-/]+", " ", text)
        text = re.sub(r"[^a-z0-9.()]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _command_for_intent(action: str, target_id: str, value: str = "") -> str:
        action = str(action or "").strip().lower()
        tid = json.dumps(str(target_id or ""), ensure_ascii=False)
        val = json.dumps(str(value or ""), ensure_ascii=False)
        if action in {"fill", "type", "type_string", "search"}:
            return f"type_string({tid}, {val}, false)"
        if action in {"select", "choose"}:
            return f"select({tid}, {val})"
        if action == "hover":
            return f"hover({tid})"
        if action in {"press", "press_key", "key"}:
            return f"press_key({tid}, {val})"
        if action == "click":
            return f"click({tid})"
        return f"click({tid})"

    @staticmethod
    def _sensitive_intent(label: str, selector: str) -> bool:
        text = f"{label} {selector}".lower()
        return any(token in text for token in ("password", "secret", "token", "credential", "api key", "apikey", "private key"))

    def _intent_aligned(self, parsed: Mapping[str, Any], expected: Mapping[str, Any]) -> Tuple[bool, str]:
        if not parsed.get("valid"):
            return False, "invalid AutoWebGLM action"
        expected_action = str(expected.get("action") or "").lower()
        action_map = {
            "fill": "type_string", "type": "type_string", "search": "type_string", "type_string": "type_string",
            "select": "select", "choose": "select", "click": "click", "hover": "hover",
            "press": "press_key", "press_key": "press_key", "key": "press_key",
        }
        wanted = action_map.get(expected_action, expected_action)
        actual = str(parsed.get("action") or "").lower()
        if wanted and actual != wanted:
            return False, f"action mismatch: expected {wanted}, got {actual}"
        args = list(parsed.get("args") or [])
        target_id = str(expected.get("target_id") or "")
        if target_id and (not args or str(args[0]) != target_id):
            return False, "target element id does not match vetted HIP intent"
        expected_value = str(expected.get("value") or "")
        if wanted in {"select", "type_string", "press_key"} and expected_value and len(args) >= 2:
            if self._semantic_key(args[1]) != self._semantic_key(expected_value):
                return False, "proposed value does not match vetted HIP intent"
        return True, "aligned with vetted HIP intent"

    async def primary_decide(
        self,
        *,
        page: Any,
        task: str,
        action: str,
        selector: str,
        label: str = "",
        value: str = "",
        history: Optional[Sequence[Any]] = None,
        learning: bool = False,
        complex_task: bool = True,
    ) -> Dict[str, Any]:
        """Return the primary AutoWebGLM action for one vetted HIP control intent.

        The framework is primary at the decision layer.  The caller remains the
        deterministic tool adapter that performs the action and proves exact state.
        If the model is unavailable or disagrees with the already-vetted skill intent,
        the intent is emitted in AutoWebGLM's own action protocol so browser execution
        never falls back to an unrelated orchestration framework.
        """
        if not self.enabled or not self.primary_framework or page is None:
            return {"status": "bypassed", "framework": "autowebglm", "reason": "primary framework disabled"}
        target_id = ""
        try:
            loc = page.locator(selector).first
            if await loc.count():
                target_id = str(await loc.get_attribute("data-hip-autowebglm-id") or "")
        except Exception:
            target_id = ""
        # Build once to assign data-hip-autowebglm-id to visible actionable controls.
        observation: Optional[Mapping[str, Any]] = None
        if not target_id:
            observation = await self.build_observation(page=page, task=task, history=history)
            try:
                loc = page.locator(selector).first
                if await loc.count():
                    target_id = str(await loc.get_attribute("data-hip-autowebglm-id") or "")
            except Exception:
                target_id = ""
        if observation is None:
            observation = await self.build_observation(page=page, task=task, history=history)
        expected = {
            "action": str(action or ""),
            "target_id": target_id,
            "target_label": str(label or "")[:300],
            "value": "<sensitive>" if self._sensitive_intent(label, selector) else str(value or "")[:4000],
            "selector_present": bool(selector),
        }
        fallback_value = str(value or "")
        fallback_command = self._command_for_intent(action, target_id, fallback_value)
        if self._sensitive_intent(label, selector):
            return {
                "status": "deterministic_protocol_fallback",
                "framework": "autowebglm_primary",
                "source": "sensitive_value_not_sent_to_model",
                "command": fallback_command,
                "parsed": self.parse_action(fallback_command),
                "aligned": True,
                "expected_intent": mask_sensitive_data(expected),
                "tool_adapter_required": True,
            }
        try:
            decision = await asyncio.wait_for(
                self.propose(page=page, task=task, history=history, expected_intent=expected, observation=observation, learning=learning, complex_task=complex_task),
                timeout=self.max_primary_decision_seconds,
            )
        except Exception as exc:
            decision = {"status": "unavailable", "error": mask_sensitive_string(str(exc))[:1000]}
        parsed = decision.get("parsed") if isinstance(decision, Mapping) else None
        if isinstance(parsed, Mapping):
            aligned, align_reason = self._intent_aligned(parsed, expected)
            if decision.get("status") == "candidate" and aligned:
                out = dict(decision)
                out.update({
                    "framework": "autowebglm_primary",
                    "aligned": True,
                    "alignment_reason": align_reason,
                    "expected_intent": mask_sensitive_data(expected),
                    "tool_adapter_required": True,
                })
                return mask_sensitive_data(out)
        if not self.deterministic_tool_fallback:
            return {
                "status": "rejected",
                "framework": "autowebglm_primary",
                "aligned": False,
                "reason": "AutoWebGLM proposal unavailable or not aligned and deterministic protocol fallback disabled",
                "decision": mask_sensitive_data(decision),
                "expected_intent": mask_sensitive_data(expected),
            }
        return {
            "status": "deterministic_protocol_fallback",
            "framework": "autowebglm_primary",
            "source": "vetted_intent_in_autowebglm_protocol",
            "command": fallback_command,
            "parsed": self.parse_action(fallback_command),
            "aligned": True,
            "reason": "model unavailable/rejected/misaligned; using vetted intent in AutoWebGLM action protocol",
            "model_decision": mask_sensitive_data(decision),
            "expected_intent": mask_sensitive_data(expected),
            "tool_adapter_required": True,
        }

    async def propose(
        self,
        *,
        page: Any,
        task: str,
        history: Optional[Sequence[Any]] = None,
        expected_intent: Optional[Mapping[str, Any]] = None,
        observation: Optional[Mapping[str, Any]] = None,
        learning: bool = False,
        complex_task: bool = True,
    ) -> Dict[str, Any]:
        if observation is None:
            observation = await self.build_observation(page=page, task=task, history=history, expected_intent=expected_intent)
        else:
            observation = dict(observation)
            observation["expected_intent"] = mask_sensitive_data(dict(expected_intent or observation.get("expected_intent") or {}))
        if not observation.get("available"):
            return {"status": "unavailable", "observation": observation}

        # Critical completion-safety rule: recovery perception is allowed without
        # an expected intent, but action generation is not.  The screenshot that
        # exposed the Data Maps loop showed the planner being asked to "choose any
        # plausible action" when no deterministic intent existed.  That turns an
        # observer into an unconstrained navigator and can repeatedly expand/cancel
        # rows forever.  AutoWebGLM remains the PRIMARY decision framework for
        # actual actions, but only after the HIP executor has vetted an action kind
        # and target selector.
        vetted = dict(expected_intent or observation.get("expected_intent") or {})
        has_vetted_intent = bool(str(vetted.get("action") or "").strip() and vetted.get("selector_present", True))
        if not has_vetted_intent:
            return mask_sensitive_data({
                "status": "observation_only",
                "no_vetted_intent": True,
                "source": "autowebglm_protocol",
                "action_generation_allowed": False,
                "reason": "No deterministic HIP expected_intent is available; recovery is observation-only and must not invent a click.",
                "observation": observation,
            })
        if self.native_model_command:
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        *self.native_model_command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=self.timeout_seconds,
                )
                raw_in = json.dumps(observation, ensure_ascii=False).encode("utf-8")
                out, err = await asyncio.wait_for(proc.communicate(raw_in), timeout=self.timeout_seconds)
                if proc.returncode == 0:
                    command = out.decode("utf-8", errors="replace").strip().splitlines()[-1]
                    parsed = self.parse_action(command)
                    allowed, reason = self._safe_action(parsed)
                    return {"status": "candidate" if allowed else "rejected", "source": "autowebglm_native_command", "command": command, "parsed": parsed, "safe": allowed, "reason": reason, "observation": observation}
                return {"status": "unavailable", "source": "autowebglm_native_command", "error": mask_sensitive_string(err.decode('utf-8', errors='replace'))[:1000], "observation": observation}
            except Exception as exc:
                # Fall through to Dell AIA protocol planner when configured.
                native_error = mask_sensitive_string(str(exc))[:1000]
        else:
            native_error = ""

        if not self.use_existing_dell_aia or self.aia is None:
            return {"status": "observation_only", "source": "autowebglm_protocol", "native_error": native_error, "observation": observation}

        system = (
            "You are the PRIMARY AutoWebGLM-compatible HIP browser decision policy. The input contains a task, "
            "value-free simplified HTML, current viewport position, open tabs, and previous operations. "
            "Return JSON only: {\"action\":\"one_function_call\",\"reason\":\"short\",\"confidence\":0.0}. "
            "Use exactly one of the action_space functions. Element arguments must use an id from simplified_html; user_input is allowed only when genuine operator input is required. Do not invent element ids or labels. Do not choose Save, "
            "Create, Submit, Delete, Deploy, Publish, Update, Confirm, Migrate or any final mutation. "
            "When expected_intent is present, your one action MUST align with its action kind and target element id. "
            "The deterministic HIP/DDS executor is the verified tool adapter and AgentQ/exact DOM checks independently gate the result."
        )
        prompt = json.dumps(observation, ensure_ascii=False, default=str)[: self.max_prompt_chars]
        try:
            if self.model_portfolio is not None:
                routed = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.model_portfolio.tournament_text,
                        system=system, task=prompt, role="action_selection", expected_json=True,
                        require_keys=("action", "confidence"), exploration=bool(learning),
                        learning=bool(learning), complex_task=bool(complex_task),
                        force_multi_model=bool(learning or complex_task),
                    ),
                    timeout=max(self.timeout_seconds, self.max_primary_decision_seconds),
                )
                decision = dict(routed.get("parsed") or {}) if routed.get("used") else {}
                model_routing = routed
            else:
                decision = await asyncio.wait_for(
                    asyncio.to_thread(self.aia.json_decision, system, prompt), timeout=self.timeout_seconds
                )
                model_routing = {"used": False, "fallback": "single_aia_client"}
            command = str((decision or {}).get("action") or "")
            parsed = self.parse_action(command)
            allowed, reason = self._safe_action(parsed)
            return mask_sensitive_data({
                "status": "candidate" if allowed else "rejected",
                "source": "autowebglm_protocol_via_dell_aia_portfolio" if self.model_portfolio is not None else "autowebglm_protocol_via_dell_aia",
                "command": command,
                "parsed": parsed,
                "safe": allowed,
                "reason": reason,
                "confidence": (decision or {}).get("confidence"),
                "planner_reason": str((decision or {}).get("reason") or "")[:1000],
                "model_routing": model_routing,
                "native_error": native_error,
                "observation": observation,
                "requires_agentq_reward_gate": self.require_agentq_reward_gate,
            })
        except Exception as exc:
            return {
                "status": "unavailable",
                "source": "autowebglm_protocol_via_dell_aia",
                "error": mask_sensitive_string(str(exc))[:1000],
                "native_error": native_error,
                "observation": observation,
            }

    def record_downstream_outcome(
        self, *, decision: Mapping[str, Any], success: bool, reward: Optional[float] = None, drift: bool = False
    ) -> Dict[str, Any]:
        """Reward the models that participated in this exact browser decision.

        The model tournament is advisory; durable champion stats are updated only
        after the physical browser action has produced verified downstream evidence.
        """
        if self.model_portfolio is None or not isinstance(decision, Mapping):
            return {"updated": False, "reason": "portfolio_unavailable"}
        trace = decision.get("model_routing")
        if not isinstance(trace, Mapping) or not bool(trace.get("used")):
            return {"updated": False, "reason": "no_portfolio_trace"}
        bounded_reward = float(1.0 if success else 0.0) if reward is None else max(0.0, min(1.0, float(reward)))
        try:
            manifest = self.model_portfolio.record_downstream_outcome(
                trace=trace, success=bool(success), reward=bounded_reward,
                role="action_selection", drift=bool(drift),
            )
            return {"updated": True, "success": bool(success), "reward": bounded_reward, "manifest": manifest}
        except Exception as exc:
            return {"updated": False, "reason": "reward_error_fail_open", "error": mask_sensitive_string(str(exc))[:500]}



# Backward-compatible class name retained above; new code may import this alias.
AutoWebGLMPrimaryFramework = AutoWebGLMRecoveryBridge
