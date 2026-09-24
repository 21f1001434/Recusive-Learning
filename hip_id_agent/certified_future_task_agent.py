from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .agentq_runtime import HIPAgentQController
from .aia_client import AIAClient
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .full_deep_learning import FAMILY_SEQUENCE, HIPCapabilityCertifier
from .future_task_agent import (
    ACTION_ALIASES,
    FAMILY_ALIASES,
    HIPFutureTaskExecutor,
    HIPFutureTaskPlanner,
    MUTATION_CONFIRMATION,
    _family_url,
    _norm,
    infer_actions,
    infer_entity,
)
from .models import utc_now
from .mutation_outcome import classify_mutation_outcome, WRITE_METHODS
from .portal_discovery_flow import (
    HIPPortalDiscoveryFlow,
    _action_label,
    _event_dict,
    _expand_candidate,
    _page_surface,
    _search_candidate,
    _shape,
)
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .semantic_affordance import canonical_intent


CERTIFIED_TASK_SCHEMA = "hip.certified-future-task.v2"
CERTIFIED_EXECUTION_SCHEMA = "hip.certified-future-task-execution.v2"


def _family_mentions(task: str) -> List[Tuple[int, str]]:
    text = str(task or "").lower()
    found: List[Tuple[int, str]] = []
    for family, aliases in FAMILY_ALIASES.items():
        best = None
        for alias in sorted(aliases, key=len, reverse=True):
            pos = text.find(alias)
            if pos >= 0 and (best is None or pos < best):
                best = pos
        if best is not None:
            found.append((best, family))
    found.sort()
    return found


def infer_families(task: str) -> List[str]:
    ordered = [family for _, family in _family_mentions(task)]
    if ordered:
        return list(dict.fromkeys(ordered))
    # Reuse the legacy single-family inference through the planner when no strong
    # literal family mention exists.
    from .future_task_agent import infer_family

    one = infer_family(task)
    return [one] if one else []


def _segment_task(task: str, family: str) -> str:
    """Return the contiguous clause group most likely to belong to ``family``.

    Follow-up clauses like ``then Edit`` inherit the most recent explicit family
    until another supported HIP family is mentioned. This preserves natural task
    phrasing without allowing an LLM to invent family boundaries.
    """
    text = str(task or "")
    clauses = [c.strip(" ,") for c in re.split(r"(?:\bthen\b|\band then\b|;|\n)", text, flags=re.I) if c.strip(" ,")]
    if not clauses:
        return text
    all_aliases = {fam: [a.lower() for a in aliases] for fam, aliases in FAMILY_ALIASES.items()}
    active = ""
    groups: Dict[str, List[str]] = {fam: [] for fam in FAMILY_ALIASES}
    for clause in clauses:
        lower = clause.lower()
        mentioned = [fam for fam, aliases in all_aliases.items() if any(a in lower for a in aliases)]
        if mentioned:
            active = mentioned[0]
        if active:
            groups.setdefault(active, []).append(clause)
    selected = groups.get(family) or []
    return " then ".join(selected) if selected else text


def _entity_for_family(task: str, family: str) -> str:
    text = str(task or "")
    aliases = sorted(FAMILY_ALIASES.get(family, ()), key=len, reverse=True)
    for alias in aliases:
        # Family-adjacent quoted entity: rule "ABC", data map 'ABC', etc.
        m = re.search(rf"{re.escape(alias)}\s+(?:named\s+)?[\"']([^\"']{{2,}})[\"']", text, flags=re.I)
        if m:
            return m.group(1).strip()
    return infer_entity(_segment_task(task, family), family)


def _structural_surface_fingerprint(surface: Mapping[str, Any]) -> str:
    controls = []
    for c in surface.get("controls") or []:
        if not isinstance(c, Mapping):
            continue
        controls.append({
            "tag": c.get("tag"), "role": c.get("role"), "type": c.get("type"),
            "label": c.get("label"), "placeholder": c.get("placeholder"),
            "formControlName": c.get("formControlName"), "expanded": c.get("expanded"),
            "required": c.get("required"), "disabled": c.get("disabled"),
        })
    actions = []
    for a in surface.get("actions") or []:
        if not isinstance(a, Mapping):
            continue
        actions.append({
            "role": a.get("role"), "label": _action_label(a), "expanded": a.get("expanded"),
            "disabled": a.get("disabled"),
        })
    dialogs = []
    for d in surface.get("dialogs") or []:
        if isinstance(d, Mapping):
            dialogs.append({"tag": d.get("tag"), "role": d.get("role"), "controlCount": d.get("controlCount")})
    payload = {"controls": controls, "actions": actions, "dialogs": dialogs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def _candidate_matches_action(action: Mapping[str, Any], action_name: str, entity: str = "") -> int:
    label = _action_label(action)
    norm_label = _norm(label)
    aliases = {_norm(x) for x in ACTION_ALIASES.get(action_name, {action_name})}
    score = 0
    if any(alias and (alias == norm_label or alias in norm_label or norm_label in alias) for alias in aliases):
        score += 12
    if action_name == "expand" and str(action.get("expanded") or "").lower() in {"true", "false"}:
        score += 10
    scope = str(action.get("scope") or "")
    if entity and entity.lower() in scope.lower():
        score += 9
    if action.get("selector"):
        score += 2
    if action.get("disabled"):
        score -= 100
    return score


class CertifiedHIPFutureTaskPlanner:
    """Plan arbitrary future HIP tasks against certified learned knowledge.

    The planner separates certification from task planning. A family can be
    operationally certified while a particular future task still requires live
    semantic exploration because the requested action was not previously visible.
    """

    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        self.legacy = HIPFutureTaskPlanner(config, graph)

    def _task_replay_match(self, subtasks: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        requested: List[Tuple[str, str]] = []
        for sub in subtasks:
            family = str(sub.get("page_family") or "")
            for step in sub.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                typ = str(step.get("type") or "")
                action = str(step.get("action") or typ)
                if typ in {"search", "expand", "action"}:
                    requested.append((family, _norm(action)))
        if not requested:
            return None
        for replay in self.graph.task_replay_profiles(verified_only=True):
            learned = [
                (str(step.get("page_family") or ""), _norm(step.get("action") or step.get("type") or ""))
                for step in replay.get("steps") or [] if isinstance(step, Mapping)
            ]
            if learned and all(pair in requested for pair in learned):
                return replay
        return None

    def plan(self, task: str, *, allow_adaptive_exploration: bool = True) -> Dict[str, Any]:
        families = infer_families(task)
        certification = HIPCapabilityCertifier(self.graph).certify()
        if not families:
            return mask_sensitive_data({
                "schema_version": CERTIFIED_TASK_SCHEMA,
                "pass": False,
                "task": task,
                "reason": "Could not infer a supported HIP portal family",
                "supported_families": list(FAMILY_SEQUENCE),
                "subtasks": [],
                "certification": certification,
            })

        subtasks: List[Dict[str, Any]] = []
        mutation_required = False
        unresolved_total = 0
        for family in families:
            segment = _segment_task(task, family)
            plan = self.legacy.plan(segment, family_hint=family)
            entity = _entity_for_family(task, family)
            if entity:
                plan["entity"] = entity
                for step in plan.get("steps") or []:
                    if isinstance(step, dict):
                        if step.get("type") == "search":
                            step["value"] = entity
                        if step.get("type") in {"expand", "action", "unresolved_action"}:
                            step["entity"] = entity
            family_cert = ((certification.get("families") or {}).get(family) or {})
            certified = bool(family_cert.get("operational_ready"))
            unresolved = list(plan.get("unresolved") or [])
            unresolved_total += len(unresolved)
            for step in plan.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                cid = str(step.get("capability_id") or "")
                cap = self.graph.data.get("capabilities", {}).get(cid, {}) if cid else {}
                risk = str(step.get("risk") or cap.get("risk") or "")
                if risk == "mutation" or classify_risk(str(step.get("action") or "")) == "mutation":
                    mutation_required = True
            if plan.get("execution_mode") == "deterministic_replay" and certified and not unresolved:
                mode = "certified_deterministic_replay"
            elif certified and not unresolved:
                mode = "certified_capability_exploitation"
            else:
                mode = "adaptive_exploration_required"
            subtasks.append(mask_sensitive_data({
                **plan,
                "segment": segment,
                "page_family": family,
                "certified_family_ready": certified,
                "family_certification": family_cert,
                "execution_mode": mode,
                "adaptive_exploration_allowed": bool(allow_adaptive_exploration),
            }))

        task_replay = self._task_replay_match(subtasks)
        can_execute = bool(subtasks) and all(
            bool(s.get("pass")) or bool(allow_adaptive_exploration)
            for s in subtasks
        )
        return mask_sensitive_data({
            "schema_version": CERTIFIED_TASK_SCHEMA,
            "pass": can_execute,
            "task": task,
            "families": families,
            "subtasks": subtasks,
            "execution_mode": "certified_task_replay" if task_replay else (
                "adaptive_mixed" if any(s.get("execution_mode") == "adaptive_exploration_required" for s in subtasks)
                else "certified_exploitation"
            ),
            "task_replay_profile_id": str((task_replay or {}).get("task_replay_profile_id") or ""),
            "mutation_required": mutation_required,
            "unresolved_step_count": unresolved_total,
            "adaptive_exploration_allowed": bool(allow_adaptive_exploration),
            "certification": certification,
            "planner": "certification_gate+capability_graph+autogen_0.7.5_guarded",
            "autogen_planning_used": any(bool(s.get("autogen_used")) for s in subtasks),
            "values_stored": False,
        })


class CertifiedHIPFutureTaskExecutor:
    """Execute certified HIP tasks with drift-aware adaptive recovery.

    Deterministic replay/capability exploitation is always attempted first.
    Read/draft action failures may be semantically rebound from the live DOM and
    optionally ranked by AutoGen. Mutation actions are never automatically retried
    after a click has been attempted because the backend outcome could be ambiguous.
    """

    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config = config
        self.graph = graph
        self.legacy_executor = HIPFutureTaskExecutor(config, graph)
        self.discovery = HIPPortalDiscoveryFlow(config, capability_graph=graph)

    def _aggregate_mutation_gate(self, plan: Mapping[str, Any], *, allow_portal_mutation: bool, confirmation: str) -> Dict[str, Any]:
        pseudo = {"steps": []}
        for sub in plan.get("subtasks") or []:
            if not isinstance(sub, Mapping):
                continue
            for raw in sub.get("steps") or []:
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                if not row.get("risk") and classify_risk(str(row.get("action") or row.get("type") or "")) == "mutation":
                    row["risk"] = "mutation"
                pseudo["steps"].append(row)
        return self.legacy_executor._mutation_gate(
            pseudo,
            allow_portal_mutation=allow_portal_mutation,
            confirmation=confirmation,
        )

    async def _fresh_mcp_assurance(self, browser: BrowserSession, agentq: HIPAgentQController, *, family: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"page_family": family, "playwright_mcp": False, "chrome_devtools_mcp": False, "hip_intelligence_mcp": False}
        try:
            if browser.playwright_mcp_backend is not None:
                snap = await browser.playwright_mcp_backend.snapshot(boxes=False, depth=5)
                out["playwright_mcp"] = bool(snap)
                out["playwright_mcp_url"] = await browser.playwright_mcp_backend.get_current_url()
        except Exception as exc:
            out["playwright_mcp_error"] = mask_sensitive_string(str(exc))
        try:
            if browser.mcp_backend is not None:
                url = await browser.mcp_backend.get_current_url()
                out["chrome_devtools_mcp"] = bool(url)
                out["chrome_devtools_mcp_url"] = url
        except Exception as exc:
            out["chrome_devtools_mcp_error"] = mask_sensitive_string(str(exc))
        try:
            surface = await _page_surface(browser.page)
            rep = await agentq.represent(page=browser.page, phase=f"future_task_{family}", controls=list(surface.get("controls") or []))
            out["hip_intelligence_mcp"] = bool(rep.get("structural_fingerprint") or rep.get("state_fingerprint"))
            out["representation_fingerprint"] = rep.get("structural_fingerprint") or rep.get("state_fingerprint")
        except Exception as exc:
            out["hip_intelligence_mcp_error"] = mask_sensitive_string(str(exc))
        out["pass"] = bool(out["playwright_mcp"] and out["chrome_devtools_mcp"] and out["hip_intelligence_mcp"])
        return mask_sensitive_data(out)

    async def _autogen_rank_candidate(self, *, task: str, family: str, action_name: str, entity: str, candidates: List[Dict[str, Any]]) -> int:
        if not candidates:
            return -1
        if not bool(getattr(self.config.aia, "enabled", False)):
            return 0
        try:
            aia = AIAClient(self.config.aia)
            safe_candidates = [
                {
                    "index": i,
                    "label": _action_label(c),
                    "role": c.get("role"),
                    "expanded": c.get("expanded"),
                    "has_selector": bool(c.get("selector")),
                    "risk": classify_risk(_action_label(c)),
                    "entity_scope_match": bool(entity and entity.lower() in str(c.get("scope") or "").lower()),
                }
                for i, c in enumerate(candidates)
            ]
            response = aia.json_decision(
                "You are the recovery planner for a Dell HIP browser agent. Return strict JSON only with candidate_index and reason. Choose only from supplied candidate indexes. Prefer exact semantic label and entity-row scope. Never choose a mutation candidate unless the requested action itself is mutation.",
                json.dumps({"task": task, "family": family, "requested_action": action_name, "entity": bool(entity), "candidates": safe_candidates}, ensure_ascii=False),
            )
            idx = int(response.get("candidate_index")) if isinstance(response, Mapping) and str(response.get("candidate_index", "")).lstrip("-").isdigit() else -1
            return idx if 0 <= idx < len(candidates) else 0
        except Exception:
            return 0

    async def _adaptive_bind(
        self,
        *,
        browser: BrowserSession,
        family: str,
        task: str,
        step: Mapping[str, Any],
        entity: str,
        run_id: str,
        output_dir: Path,
    ) -> Tuple[Any, str, Dict[str, Any]]:
        surface = await _page_surface(browser.page)
        before_fp = _structural_surface_fingerprint(surface)
        # Persist the live drifted surface into the capability graph before acting.
        learned = await self.discovery._learn_surface(
            browser=browser, family=family, run_id=run_id,
            output_dir=output_dir / "adaptive_surface",
        )
        typ = str(step.get("type") or "")
        action_name = str(step.get("action") or typ)
        selected: Optional[Dict[str, Any]] = None
        if typ == "search":
            selected = _search_candidate(surface)
        elif typ == "expand":
            selected = _expand_candidate(surface, entity)
        else:
            scored: List[Tuple[int, Dict[str, Any]]] = []
            for raw in surface.get("actions") or []:
                if not isinstance(raw, Mapping):
                    continue
                score = _candidate_matches_action(raw, action_name, entity)
                if score > 0:
                    scored.append((score, dict(raw)))
            scored.sort(key=lambda x: -x[0])
            candidates = [x[1] for x in scored[:12]]
            idx = await self._autogen_rank_candidate(task=task, family=family, action_name=action_name, entity=entity, candidates=candidates)
            if idx >= 0:
                selected = candidates[idx]
        if not selected:
            raise RuntimeError(f"Adaptive exploration could not find a candidate for {family}:{typ}:{action_name}")

        label = _action_label(selected) or ("Search" if typ == "search" else action_name)
        risk = classify_risk(label)
        requested_risk = classify_risk(action_name)
        if risk == "mutation" and requested_risk != "mutation":
            raise RuntimeError("Adaptive recovery rejected a mutation candidate for a non-mutation request")
        cap = self.graph.observe_capability(
            page_family=family,
            kind="search" if typ == "search" else ("expand" if typ == "expand" else "row_action"),
            label=label,
            selector=str(selected.get("selector") or ""),
            role=str(selected.get("role") or ""),
            placeholder=str(selected.get("placeholder") or ""),
            scope="listing_surface" if typ == "search" else "entity_row",
            risk=risk,
            run_id=run_id,
            evidence={"adaptive_rebind": True, "structural_fingerprint": before_fp, "url": surface.get("url")},
        )
        selector = str(selected.get("selector") or "")
        locator = browser.page.locator(selector).first if selector else None
        if locator is None or not await locator.count():
            locator, selector = await self.legacy_executor._locator_for_capability(browser.page, cap, entity=entity)
        return locator, selector, {"capability": cap, "surface_fingerprint": before_fp, "learned_surface": learned}

    async def _verify_effect(
        self,
        *,
        browser: BrowserSession,
        step: Mapping[str, Any],
        entity: str,
        before_surface: Mapping[str, Any],
        network_rows: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        after = await _page_surface(browser.page)
        before_fp = _structural_surface_fingerprint(before_surface)
        after_fp = _structural_surface_fingerprint(after)
        typ = str(step.get("type") or "")
        action = _norm(step.get("action") or typ)
        checks: Dict[str, Any] = {
            "structural_change": before_fp != after_fp,
            "network_observed": bool(network_rows),
            "before_fingerprint": before_fp,
            "after_fingerprint": after_fp,
        }
        if typ == "search":
            rows_text = "\n".join(str(r.get("text") or "") for r in after.get("rows") or [] if isinstance(r, Mapping))
            checks["entity_visible"] = bool(entity and entity.lower() in rows_text.lower())
            passed = checks["entity_visible"] or checks["network_observed"]
        elif typ == "expand":
            row_actions = [a for a in after.get("actions") or [] if isinstance(a, Mapping) and entity and entity.lower() in str(a.get("scope") or "").lower()]
            checks["row_actions_revealed"] = bool(row_actions)
            passed = checks["row_actions_revealed"] or checks["structural_change"]
        elif action in {"edit", "clone", "view", "details", "detail", "history", "audit", "open"}:
            checks["dialog_or_form_visible"] = bool(after.get("dialogs"))
            passed = checks["dialog_or_form_visible"] or checks["structural_change"] or checks["network_observed"]
        elif classify_risk(action) == "mutation":
            # Mutation completion is accepted only with concrete network evidence;
            # it is never inferred merely from a DOM change.
            mutation_responses = [r for r in network_rows if str(r.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"} and r.get("status") is not None]
            successful_mutation_responses = []
            for row in mutation_responses:
                try:
                    if 200 <= int(row.get("status")) < 300:
                        successful_mutation_responses.append(row)
                except Exception:
                    pass
            checks["mutation_response_observed"] = bool(mutation_responses)
            checks["mutation_2xx_response_observed"] = bool(successful_mutation_responses)
            passed = checks["mutation_2xx_response_observed"]
        else:
            passed = checks["structural_change"] or checks["network_observed"]
        return mask_sensitive_data({"pass": bool(passed), "checks": checks, "after_surface": after})

    @staticmethod
    def _network_rows_since(
        browser: BrowserSession, start_index: int, *, capability_id: str = "",
        dispatch: Mapping[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Return action network evidence fenced to the physical dispatch epoch.

        Background polling and preflight requests can occur after ``start_index``
        but before the mutation click. When dispatch evidence is available, only
        requests first observed after that boundary and on the same route/stage
        are eligible to prove mutation outcome.
        """
        dispatch = dict(dispatch or {})
        dispatched = bool(dispatch.get("dispatch_attempted"))
        before_ids = {str(x) for x in (dispatch.get("network_request_ids_at_dispatch") or []) if str(x)}
        dispatch_url = str(dispatch.get("dispatch_page_url") or "")
        dispatch_stage = str(dispatch.get("dispatch_stage") or "")
        rows_by_id: Dict[str, Dict[str, Any]] = {}

        def _eligible(event: Mapping[str, Any]) -> bool:
            request_id = str(event.get("request_id") or "")
            if dispatched and request_id and request_id in before_ids:
                return False
            page_context = str(event.get("page_context") or "")
            if dispatched and dispatch_url and page_context and not BrowserSession._surface_url_matches(dispatch_url, page_context):
                return False
            stage = str(event.get("stage") or "")
            if dispatched and dispatch_stage and stage and stage != dispatch_stage:
                return False
            return True

        for event in [_event_dict(x) for x in browser.network_tab_events[max(0, int(start_index)):]]:
            if not _eligible(event):
                continue
            request_id = str(event.get("request_id") or "")
            row = mask_sensitive_data({
                "request_id": request_id,
                "method": event.get("method"),
                "url": event.get("url"),
                "status": event.get("status"),
                "request_payload": event.get("request_body_redacted"),
                "response_payload": event.get("response_body_redacted") if event.get("response_body_redacted") is not None else event.get("response_body_text_redacted"),
                "page_context": event.get("page_context"),
                "caused_by_capability_id": capability_id,
                "caused_by_dispatch": bool(dispatched),
                "causality_basis": "post_dispatch_request_id_and_route" if dispatched else "action_window",
                "stage": "certified_future_task",
            })
            rows_by_id[request_id or f"event-{len(rows_by_id)}"] = row

        cdp_requests = getattr(browser, "_cdp_requests", {}) or {}
        for request_id, base in cdp_requests.items():
            request_id = str(request_id or "")
            event = {**dict(base or {}), "request_id": request_id}
            if not _eligible(event) or request_id in rows_by_id:
                continue
            method = str(event.get("method") or "").upper()
            if method not in WRITE_METHODS:
                continue
            rows_by_id[request_id] = mask_sensitive_data({
                "request_id": request_id,
                "method": method,
                "url": event.get("url"),
                "status": event.get("status"),
                "request_payload": event.get("request_body_redacted"),
                "response_payload": None,
                "page_context": event.get("page_context"),
                "caused_by_capability_id": capability_id,
                "caused_by_dispatch": bool(dispatched),
                "causality_basis": "post_dispatch_cdp_request_and_route" if dispatched else "action_window_cdp",
                "stage": "certified_future_task",
            })
        return list(rows_by_id.values())

    async def _reconcile_mutation_outcome(
        self,
        *,
        browser: BrowserSession,
        step: Mapping[str, Any],
        entity: str,
        before_surface: Mapping[str, Any],
        start_net: int,
        capability_id: str,
        action_error: str = "",
    ) -> Dict[str, Any]:
        gov = self.config.governance
        dispatch = browser.last_click_dispatch_evidence()
        timeout = max(0.0, float(getattr(gov, "mutation_reconciliation_timeout_seconds", 3.0) or 3.0))
        poll = max(0.05, float(getattr(gov, "mutation_reconciliation_poll_seconds", 0.25) or 0.25))

        # A provable pre-dispatch failure needs no network wait. Once a physical
        # click was attempted, observe read-only evidence for a short bounded window
        # because HIP may finish the request after the browser tool has timed out.
        deadline = asyncio.get_event_loop().time() + (timeout if dispatch.get("dispatch_attempted") else 0.0)
        network_rows: List[Dict[str, Any]] = []
        while True:
            network_rows = self._network_rows_since(
                browser, start_net, capability_id=capability_id, dispatch=dispatch
            )
            writes = [row for row in network_rows if str(row.get("method") or "").upper() in WRITE_METHODS]
            terminal = [row for row in writes if row.get("status") is not None]
            # A terminal response is authoritative enough to classify immediately.
            if terminal or asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(poll)

        after_surface = await _page_surface(browser.page)
        structural_change = _structural_surface_fingerprint(before_surface) != _structural_surface_fingerprint(after_surface)
        ui_signal = await browser.mutation_ui_signal(
            action=str(step.get("action") or step.get("type") or ""), entity=entity
        )
        outcome = classify_mutation_outcome(
            dispatch=dispatch, network_rows=network_rows, ui_signal=ui_signal,
            structural_change=structural_change, action_error=action_error,
        )
        guard_resolution = browser.resolve_mutation_dispatch_guard(outcome)
        return mask_sensitive_data({
            **outcome,
            "mutation_dispatch_guard": guard_resolution,
            "network_transactions": network_rows,
            "ui_signal": ui_signal,
            "before_fingerprint": _structural_surface_fingerprint(before_surface),
            "after_fingerprint": _structural_surface_fingerprint(after_surface),
            "reconciliation_timeout_seconds": timeout,
            "read_only_reconciliation": True,
        })

    async def execute(
        self,
        *,
        task: str,
        plan: Mapping[str, Any],
        run_dir: str | Path,
        allow_portal_mutation: bool = False,
        confirmation: str = "",
        allow_adaptive_exploration: bool = True,
    ) -> Dict[str, Any]:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        task_id = f"cert-task-{uuid.uuid4().hex[:12]}"
        gate = self._aggregate_mutation_gate(plan, allow_portal_mutation=allow_portal_mutation, confirmation=confirmation)
        safe_write_json(run_dir / "certified_mutation_gate.json", gate)
        if not gate.get("pass"):
            result = {"schema_version": CERTIFIED_EXECUTION_SCHEMA, "pass": False, "status": "blocked_mutation_authorization", "mutation_gate": gate, "plan": plan}
            safe_write_json(run_dir / "certified_future_task_execution.json", result)
            return mask_sensitive_data(result)

        cert = HIPCapabilityCertifier(self.graph).certify()
        safe_write_json(run_dir / "certification_preflight.json", cert)
        result: Dict[str, Any] = {
            "schema_version": CERTIFIED_EXECUTION_SCHEMA,
            "task_id": task_id,
            "task": task,
            "started_at": utc_now(),
            "plan_schema": plan.get("schema_version"),
            "steps": [],
            "subtasks": [],
            "mutation_gate": gate,
            "certification_preflight": cert,
            "adaptive_exploration_used": False,
            "values_stored": False,
        }
        all_replay_steps: List[Dict[str, Any]] = []
        agentq = HIPAgentQController(memory_root=Path(self.config.reporting.memory_dir), run_dir=run_dir / "agentq_future_task", config=self.config)

        async with BrowserSession(self.config, run_dir) as browser:
            await agentq.start()
            if gate.get("mutation_required"):
                browser.set_portal_mutation_authorization(enabled=True, allowed_labels=gate.get("allowed_labels") or [], task_id=task_id)
            try:
                for sub_index, sub in enumerate(plan.get("subtasks") or []):
                    if not isinstance(sub, Mapping):
                        continue
                    family = str(sub.get("page_family") or "")
                    entity = str(sub.get("entity") or "")
                    sub_result: Dict[str, Any] = {"page_family": family, "entity_present": bool(entity), "steps": [], "execution_mode": sub.get("execution_mode")}
                    # Navigate once per family.
                    target_url = _family_url(family)
                    await browser.goto_base_and_complete_sso(target_url)
                    nav_surface = await _page_surface(browser.page)
                    sub_result["navigation_fingerprint"] = _structural_surface_fingerprint(nav_surface)
                    all_replay_steps.append({"type": "navigate", "page_family": family, "action": "navigate", "value_source": "page_family.url", "verification": "target_family_surface"})

                    for step_index, step_raw in enumerate(sub.get("steps") or []):
                        if not isinstance(step_raw, Mapping):
                            continue
                        step = dict(step_raw)
                        if step.get("type") == "navigate":
                            continue
                        typ = str(step.get("type") or "")
                        cid = str(step.get("capability_id") or "")
                        cap = self.graph.data.get("capabilities", {}).get(cid) if cid else None
                        before_surface = await _page_surface(browser.page)
                        start_net = len(browser.network_tab_events)
                        adaptive = False
                        click_attempted = False
                        bind_evidence: Dict[str, Any] = {}
                        semantic_direct = typ == "semantic_action"
                        locator = None
                        selector = ""
                        semantic_intent = ""
                        semantic_aliases: List[str] = []

                        if semantic_direct:
                            action_name = str(step.get("action") or "")
                            semantic_intent = canonical_intent(action_name)
                            semantic_aliases = [x for x in [action_name, entity] if x]
                            for hint in (step.get("world_model_hints") or [])[:8]:
                                if not isinstance(hint, Mapping):
                                    continue
                                ctrl = hint.get("control") if isinstance(hint.get("control"), Mapping) else {}
                                for value in (ctrl.get("label"), ctrl.get("section")):
                                    if value and str(value) not in semantic_aliases:
                                        semantic_aliases.append(str(value))
                            bind_evidence = {
                                "binding": "live_semantic_affordance",
                                "intent": semantic_intent,
                                "aliases": semantic_aliases,
                                "world_model_hint_count": len(step.get("world_model_hints") or []),
                                "requires_live_reproof": True,
                                "memory_is_advisory": True,
                            }
                            adaptive = True
                            result["adaptive_exploration_used"] = True
                        else:
                            try:
                                if isinstance(cap, Mapping):
                                    locator, selector = await self.legacy_executor._locator_for_capability(browser.page, cap, entity=entity)
                                else:
                                    raise RuntimeError("planned capability is unresolved or absent from graph")
                            except Exception as bind_exc:
                                if not allow_adaptive_exploration:
                                    raise
                                locator, selector, bind_evidence = await self._adaptive_bind(
                                    browser=browser, family=family, task=task, step=step,
                                    entity=entity, run_id=task_id,
                                    output_dir=run_dir / "adaptive_recovery" / f"{sub_index:02d}_{step_index:03d}",
                                )
                                cap = bind_evidence.get("capability") or cap
                                cid = str((cap or {}).get("capability_id") or "")
                                adaptive = True
                                result["adaptive_exploration_used"] = True
                                bind_evidence["initial_bind_error"] = mask_sensitive_string(str(bind_exc))

                        risk = str(step.get("risk") or (cap or {}).get("risk") or classify_risk(step.get("action") or typ))
                        mutation_reconciliation: Dict[str, Any] = {}
                        pre_dispatch_rebind = False

                        async def _invoke_current_binding(*, recovered: bool = False) -> None:
                            nonlocal click_attempted, semantic_intent
                            if typ == "search":
                                await browser.fill_and_log(
                                    locator=locator,
                                    value=entity or str(step.get("value") or ""),
                                    selector=selector,
                                    action_type="certified_future_search_recovered" if recovered else "certified_future_search",
                                )
                                try:
                                    await browser.press_and_log(locator=locator, key="Enter", selector=selector)
                                except Exception:
                                    pass
                            elif semantic_direct:
                                before_dispatch = browser.last_click_dispatch_evidence()
                                try:
                                    await browser.click_semantic_affordance(
                                        intent=semantic_intent, aliases=semantic_aliases,
                                        allow_mutation=(risk == "mutation"),
                                        action_label=str(step.get("action") or semantic_intent),
                                        allow_compound_menu=True,
                                    )
                                except Exception as semantic_exc:
                                    # A generic Create request may first need to enter the
                                    # same-page Add/Create drawer. This is a structural transition,
                                    # not a remembered-selector retry. Never do it after a mutation
                                    # dispatch has been attempted.
                                    after_dispatch = browser.last_click_dispatch_evidence()
                                    fresh_dispatch = after_dispatch != before_dispatch
                                    dispatched = bool(fresh_dispatch and after_dispatch.get("dispatch_attempted"))
                                    if semantic_intent == "create" and not dispatched:
                                        await browser.click_semantic_affordance(
                                            intent="open_add_form", aliases=["Add", "Create", entity],
                                            allow_mutation=False, action_label="Open create form",
                                            allow_compound_menu=False,
                                        )
                                        semantic_intent = "open_add_form"
                                        bind_evidence["initial_semantic_error"] = mask_sensitive_string(str(semantic_exc))
                                    else:
                                        raise
                                final_dispatch = browser.last_click_dispatch_evidence()
                                click_attempted = bool(final_dispatch != before_dispatch and final_dispatch.get("dispatch_attempted"))
                            else:
                                click_attempted = True
                                await browser.click_and_wait(
                                    action=str((cap or {}).get("label") or step.get("action") or typ),
                                    locator=locator,
                                    selector=selector,
                                    mutation_risk=(risk == "mutation"),
                                )
                            await asyncio.sleep(0.45)

                        try:
                            await _invoke_current_binding()
                        except Exception as action_exc:
                            if risk == "mutation" and click_attempted:
                                # First inspect dispatch evidence. A mutation that failed before
                                # any physical click may be safely rebound once; after dispatch,
                                # no executor fallback or second click is allowed.
                                dispatch = browser.last_click_dispatch_evidence()
                                if (
                                    not bool(dispatch.get("dispatch_attempted"))
                                    and bool(getattr(self.config.governance, "allow_pre_dispatch_rebind", True))
                                    and allow_adaptive_exploration
                                    and not adaptive
                                ):
                                    locator, selector, bind_evidence = await self._adaptive_bind(
                                        browser=browser, family=family, task=task, step=step,
                                        entity=entity, run_id=task_id,
                                        output_dir=run_dir / "adaptive_recovery" / f"{sub_index:02d}_{step_index:03d}_predispatch",
                                    )
                                    cap = bind_evidence.get("capability") or cap
                                    cid = str((cap or {}).get("capability_id") or "")
                                    adaptive = True
                                    pre_dispatch_rebind = True
                                    result["adaptive_exploration_used"] = True
                                    bind_evidence["pre_dispatch_bind_error"] = mask_sensitive_string(str(action_exc))
                                    try:
                                        await _invoke_current_binding(recovered=True)
                                    except Exception as rebound_exc:
                                        mutation_reconciliation = await self._reconcile_mutation_outcome(
                                            browser=browser, step=step, entity=entity,
                                            before_surface=before_surface, start_net=start_net,
                                            capability_id=cid, action_error=str(rebound_exc),
                                        )
                                        if not mutation_reconciliation.get("pass"):
                                            failed_step = mask_sensitive_data({
                                                "type": typ, "action": step.get("action"), "pass": False,
                                                "capability_id": cid, "label": (cap or {}).get("label"), "risk": risk,
                                                "adaptive_rebind": adaptive, "pre_dispatch_rebind": pre_dispatch_rebind,
                                                "selector_used": selector, "bind_evidence": bind_evidence,
                                                "mutation_dispatched": bool(mutation_reconciliation.get("dispatch_attempted")),
                                                "mutation_reconciliation": mutation_reconciliation,
                                                "network_transactions": mutation_reconciliation.get("network_transactions") or [],
                                            })
                                            sub_result["steps"].append(failed_step)
                                            result["steps"].append(failed_step)
                                            raise RuntimeError(
                                                f"Mutation outcome {mutation_reconciliation.get('classification')}; automatic retry prohibited: {rebound_exc}"
                                            ) from rebound_exc
                                else:
                                    mutation_reconciliation = await self._reconcile_mutation_outcome(
                                        browser=browser, step=step, entity=entity,
                                        before_surface=before_surface, start_net=start_net,
                                        capability_id=cid, action_error=str(action_exc),
                                    )
                                    if not mutation_reconciliation.get("pass"):
                                        failed_step = mask_sensitive_data({
                                            "type": typ, "action": step.get("action"), "pass": False,
                                            "capability_id": cid, "label": (cap or {}).get("label"), "risk": risk,
                                            "adaptive_rebind": adaptive, "pre_dispatch_rebind": pre_dispatch_rebind,
                                            "selector_used": selector, "bind_evidence": bind_evidence,
                                            "mutation_dispatched": bool(mutation_reconciliation.get("dispatch_attempted")),
                                            "mutation_reconciliation": mutation_reconciliation,
                                            "network_transactions": mutation_reconciliation.get("network_transactions") or [],
                                        })
                                        sub_result["steps"].append(failed_step)
                                        result["steps"].append(failed_step)
                                        raise RuntimeError(
                                            f"Mutation outcome {mutation_reconciliation.get('classification')}; automatic retry prohibited: {action_exc}"
                                        ) from action_exc
                                    # A 2xx response can arrive even if Playwright/MCP timed out
                                    # after the click. Continue without issuing any second mutation.
                            else:
                                # Read/draft deterministic capabilities may do one fresh adaptive
                                # rebind if their selector drifted. A semantic_action has already
                                # performed live semantic resolution/compound-menu traversal and is
                                # therefore never replayed through a second physical executor here.
                                if semantic_direct or adaptive or not allow_adaptive_exploration:
                                    raise
                                locator, selector, bind_evidence = await self._adaptive_bind(
                                    browser=browser, family=family, task=task, step=step,
                                    entity=entity, run_id=task_id,
                                    output_dir=run_dir / "adaptive_recovery" / f"{sub_index:02d}_{step_index:03d}_retry",
                                )
                                cap = bind_evidence.get("capability") or cap
                                cid = str((cap or {}).get("capability_id") or "")
                                adaptive = True
                                result["adaptive_exploration_used"] = True
                                await _invoke_current_binding(recovered=True)


                        events = [_event_dict(x) for x in browser.network_tab_events[start_net:]]
                        if risk == "mutation":
                            network_rows = self._network_rows_since(
                                browser, start_net, capability_id=cid,
                                dispatch=browser.last_click_dispatch_evidence(),
                            )
                        else:
                            network_rows = []
                            for e in events:
                                network_rows.append(mask_sensitive_data({
                                    "request_id": e.get("request_id"), "method": e.get("method"), "url": e.get("url"),
                                    "status": e.get("status"), "request_payload": e.get("request_body_redacted"),
                                    "response_payload": e.get("response_body_redacted") if e.get("response_body_redacted") is not None else e.get("response_body_text_redacted"),
                                    "caused_by_capability_id": cid, "stage": "certified_future_task",
                                }))
                        for e in events:
                            if cid:
                                self.graph.observe_api_contract(
                                    page_family=family, method=str(e.get("method") or "GET"), url=str(e.get("url") or ""),
                                    request_shape=_shape(e.get("request_body_redacted")), response_status=e.get("status"),
                                    response_shape=_shape(e.get("response_body_redacted") if e.get("response_body_redacted") is not None else e.get("response_body_text_redacted")),
                                    stage="certified_future_task", caused_by_capability_id=cid,
                                )
                        verification = await self._verify_effect(browser=browser, step=step, entity=entity, before_surface=before_surface, network_rows=network_rows)
                        if not verification.get("pass") and risk == "mutation":
                            mutation_reconciliation = await self._reconcile_mutation_outcome(
                                browser=browser, step=step, entity=entity,
                                before_surface=before_surface, start_net=start_net,
                                capability_id=cid, action_error="post-action verification did not prove a 2xx mutation response",
                            )
                            network_rows = mutation_reconciliation.get("network_transactions") or network_rows
                            if mutation_reconciliation.get("pass"):
                                verification = await self._verify_effect(
                                    browser=browser, step=step, entity=entity,
                                    before_surface=before_surface, network_rows=network_rows,
                                )
                            else:
                                failed_step = mask_sensitive_data({
                                    "type": typ, "action": step.get("action"), "pass": False,
                                    "capability_id": cid, "label": (cap or {}).get("label"), "risk": risk,
                                    "adaptive_rebind": adaptive, "pre_dispatch_rebind": pre_dispatch_rebind,
                                    "selector_used": selector, "bind_evidence": bind_evidence,
                                    "mutation_dispatched": bool(mutation_reconciliation.get("dispatch_attempted")),
                                    "mutation_reconciliation": mutation_reconciliation,
                                    "verification": {"pass": False, "checks": (verification.get("checks") or {})},
                                    "network_transactions": network_rows,
                                })
                                sub_result["steps"].append(failed_step)
                                result["steps"].append(failed_step)
                                raise RuntimeError(
                                    f"Post-action mutation outcome {mutation_reconciliation.get('classification')}; automatic retry prohibited"
                                )
                        if not verification.get("pass"):
                            raise RuntimeError(f"Post-action verification failed for {family}:{typ}:{step.get('action') or ''}")
                        if risk == "mutation":
                            browser.resolve_mutation_dispatch_guard({"classification": "committed_verified", "pass": True})
                        if cid:
                            self.graph.mark_success(cid)
                        dispatch_evidence = browser.last_click_dispatch_evidence() if risk == "mutation" else {}
                        step_result = mask_sensitive_data({
                            "type": typ, "action": step.get("action"), "pass": True,
                            "capability_id": cid, "label": (cap or {}).get("label"), "risk": risk,
                            "adaptive_rebind": adaptive, "pre_dispatch_rebind": pre_dispatch_rebind, "selector_used": selector,
                            "bind_evidence": bind_evidence,
                            "verification": {"pass": True, "checks": (verification.get("checks") or {})},
                            "mutation_dispatched": bool(dispatch_evidence.get("dispatch_attempted")) if risk == "mutation" else False,
                            "mutation_reconciliation": mutation_reconciliation or None,
                            "network_transactions": network_rows,
                        })
                        sub_result["steps"].append(step_result)
                        result["steps"].append(step_result)
                        all_replay_steps.append({
                            "type": typ,
                            "page_family": family,
                            "capability_id": cid,
                            "action": str(step.get("action") or typ),
                            "value_source": "current_task.entity" if typ in {"search", "expand", "action"} and entity else "",
                            "scope": "entity_row" if typ in {"expand", "action"} else "listing_surface",
                            "risk": risk,
                            "verification": "effect+api+structural_state",
                        })

                    mcp = await self._fresh_mcp_assurance(browser, agentq, family=family)
                    sub_result["mcp_assurance"] = mcp
                    sub_result["pass"] = bool(sub_result["steps"]) and all(bool(x.get("pass")) for x in sub_result["steps"]) and bool(mcp.get("pass"))
                    result["subtasks"].append(mask_sensitive_data(sub_result))
                    if not sub_result["pass"]:
                        raise RuntimeError(f"Fresh three-MCP assurance failed for {family}")
            except Exception as exc:
                result["error"] = mask_sensitive_string(str(exc))
            finally:
                browser.clear_portal_mutation_authorization()
                try:
                    await agentq.close()
                except Exception:
                    pass
                await browser.flush_logs()

        result["finished_at"] = utc_now()
        result["pass"] = bool(result.get("subtasks")) and not result.get("error") and all(bool(x.get("pass")) for x in result.get("subtasks") or [])
        if result["pass"]:
            family_set = [str(x.get("page_family") or "") for x in result.get("subtasks") or []]
            replay_name = "future_" + "_then_".join(
                _norm(step.get("action") or step.get("type") or "")
                for step in all_replay_steps if step.get("type") != "navigate"
            )[:160]
            replay = self.graph.observe_task_replay_profile(
                name=replay_name or "future_task",
                steps=all_replay_steps,
                families=family_set,
                preconditions=["certified_capability_graph", "fresh_three_mcp_assurance", "runtime_values_from_current_task"],
                verified=True,
                evidence={"task_id": task_id, "adaptive_exploration_used": result.get("adaptive_exploration_used")},
                run_id=task_id,
            )
            result["promoted_task_replay"] = replay
        self.graph.save()
        result["capability_graph_manifest"] = self.graph.manifest()
        safe_write_json(run_dir / "certified_future_task_execution.json", mask_sensitive_data(result))
        safe_write_json(run_dir / "certified_future_task_causal_trace.json", {
            "schema_version": "hip.certified-future-task-causal-trace.v1",
            "steps": [
                {"capability_id": s.get("capability_id"), "action": s.get("action") or s.get("type"), "network_transactions": s.get("network_transactions") or []}
                for s in result.get("steps") or []
            ],
            "values_stored": False,
        })
        return mask_sensitive_data(result)
