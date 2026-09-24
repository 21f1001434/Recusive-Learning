from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from .agentq_runtime import HIPAgentQController
from .autonomous_dependency_runtime import apply_dependency_execution_contract
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .datamap_deep_discovery import (
    DRAFT_INSPECT_ACTIONS,
    MUTATING_METHODS,
    MUTATION_TOKENS,
    _form_contract,
    _listing_filter_candidates,
    _pagination_candidates,
    _row_actions,
    _surface_delta,
)
from .dummy_fill_e2e import PHASE_URLS
from .models import RunContext, utc_now
from .portal_discovery_flow import (
    HIPPortalDiscoveryFlow,
    _action_label,
    _add_candidate,
    _expand_candidate,
    _norm,
    _page_surface,
    _safe_close_candidates,
    _search_candidate,
    _shape,
)
from .rules_kb import (
    _apply_rule_condition_row_adds,
    _inspect_rule_condition_rows,
    _inspect_rule_create_form_validity,
    _rule_condition_rows_exact,
    _extract_rule_condition_rows,
)
from .safe_io import safe_write_json
from .phase_vocabulary_learning import learn_complete_form_vocabulary
from .security import mask_sensitive_data, mask_sensitive_string
from .stateful_form_runtime import (
    capture_stateful_controls,
    compile_phase_state_graph,
    execute_phase_state_graph,
)

RULES_URL = PHASE_URLS["rule"]
FAMILY = "rules"
PHASE = "rule"
MUTATION_PROBE_ACTIONS = ("migrate", "deploy", "delete")


def _rule_object(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    objects = payload.get("objects") if isinstance(payload.get("objects"), Mapping) else {}
    return objects.get("rule") if isinstance(objects.get("rule"), Mapping) else {}


def _query_for(payload: Mapping[str, Any]) -> str:
    obj = _rule_object(payload)
    return str(obj.get("name") or obj.get("rule_name") or "").strip()


def _value_free_rule_blueprint(graph: Mapping[str, Any]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, Mapping):
            continue
        loc = raw.get("semantic_locator") if isinstance(raw.get("semantic_locator"), Mapping) else {}
        nodes.append({
            "node_id": raw.get("node_id"),
            "section": raw.get("section"),
            "field_key": raw.get("field_key"),
            "action": raw.get("action"),
            "input_path": raw.get("input_path"),
            "row_kind": raw.get("row_kind"),
            "row_index": raw.get("row_index"),
            "required": bool(raw.get("required")),
            "depends_on": [str(x) for x in (raw.get("depends_on") or [])],
            "verification": raw.get("verification"),
            "semantic_locator": {
                "names": list(loc.get("names") or []),
                "labels": list(loc.get("labels") or []),
                "placeholders": list(loc.get("placeholders") or []),
                "roles": list(loc.get("roles") or []),
                "section_aliases": list(loc.get("section_aliases") or []),
            },
            "expected_value_stored": False,
        })
    edges = [{
        "edge_id": e.get("edge_id"),
        "from": e.get("from"),
        "to": e.get("to"),
        "relation": e.get("relation"),
        "parent_value_source": next((n.get("input_path") for n in nodes if n.get("node_id") == e.get("from")), ""),
        "parent_value_stored": False,
    } for e in (graph.get("dependency_edges") or []) if isinstance(e, Mapping)]
    return {
        "schema_version": "hip.rule-dependency-blueprint.v1",
        "phase": "rule",
        "object_family": "rule",
        "nodes": nodes,
        "dependency_edges": edges,
        "repeatable_rows": dict(graph.get("repeatable_rows") or {}),
        "section_order": ["Rule Details", "Actions", "Conditions"],
        "critical_gates": [
            "Rule Details exact before dependent dropdowns",
            "Action Type exact before Mapping Identifier",
            "Mapping Identifier exact before Conditions +",
            "current Condition row exact before next Conditions +",
        ],
        "repeatable_policy": "input_driven_sequential_conditions",
        "values_stored": False,
    }


def _execution_value_free(execution: Mapping[str, Any]) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for raw in execution.get("attempts") or []:
        if not isinstance(raw, Mapping):
            continue
        attempts.append({
            "node_id": raw.get("node_id"),
            "field": raw.get("field"),
            "input_path": raw.get("input_path"),
            "section": raw.get("section"),
            "row_kind": raw.get("row_kind"),
            "row_index": raw.get("row_index"),
            "success": bool(raw.get("success")),
            "exact_verified": bool(raw.get("exact_verified")),
            "selector": raw.get("selector"),
            "dependencies": list(raw.get("dependencies") or []),
            "reason": raw.get("reason"),
            "values_stored": False,
        })
    return {
        "pass": bool(execution.get("pass")),
        "status": execution.get("status"),
        "graph_id": execution.get("graph_id"),
        "attempts": attempts,
        "failed_count": len(execution.get("failed_attempts") or []),
        "completed_node_count": len(execution.get("completed_node_ids") or []),
        "dependency_execution_contract": execution.get("dependency_execution_contract") or {},
        "scheduler_final_state": execution.get("scheduler_final_state") or {},
        "deterministic_replay_speed_profile": execution.get("deterministic_replay_speed_profile") or {},
        "values_stored": False,
    }


class RuleDeepDiscoveryFlow:
    """Deep-learn the Rules page and the exact unsaved Rule form transaction.

    The learner deliberately reuses the production Rules KB transaction helpers
    for condition-row creation and asynchronous Mapping Identifier commitment.
    This keeps the learned path aligned with the actual runtime instead of
    teaching a separate simplified interaction flow.
    """

    def __init__(self, config: AppConfig, *, capability_graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        graph_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = capability_graph or HIPCapabilityGraph(graph_root)
        self.discovery = HIPPortalDiscoveryFlow(config, capability_graph=self.graph)

    async def _close_surface(self, browser: BrowserSession, surface: Mapping[str, Any]) -> bool:
        page = browser.page
        if page is None:
            return False
        for close in _safe_close_candidates(surface):
            selector = str(close.get("selector") or "")
            label = _action_label(close) or "Close"
            try:
                loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
                await browser.click_and_wait(action="close Rule discovery surface", locator=loc, selector=selector)
                await asyncio.sleep(0.3)
                return True
            except Exception:
                continue
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
            return True
        except Exception:
            return False

    async def _inspect_filter(self, *, browser: BrowserSession, control: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None:
            return {"pass": False, "reason": "no page"}
        selector = str(control.get("selector") or "")
        label = str(control.get("label") or control.get("placeholder") or "Filter")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="filter", label=label, selector=selector,
            role=str(control.get("role") or ""), placeholder=str(control.get("placeholder") or ""),
            scope="listing_surface", run_id=run_id, evidence={"url": page.url})
        before = await _page_surface(page); start = len(browser.network_tab_events)
        try:
            await browser.click_and_wait(action=f"inspect Rule filter {label}", locator=page.locator(selector).first, selector=selector)
            await asyncio.sleep(0.3)
            options = await page.evaluate(r"""() => [...document.querySelectorAll('[role=option],.dds__dropdown__item-option')].filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).slice(0,1000).map(el=>String(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean)""")
            try: await page.keyboard.press("Escape")
            except Exception: pass
            after = await _page_surface(page)
            self.graph.mark_success(cap["capability_id"])
            net = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir, start_index=start, caused_by=cap["capability_id"], stage="inspect_filter")
            result = {"pass": True, "capability_id": cap["capability_id"], "option_labels": mask_sensitive_data(options or []), "network": net, "state_delta": _surface_delta(before, after), "selection_changed": False}
        except Exception as exc:
            result = {"pass": False, "capability_id": cap["capability_id"], "error": mask_sensitive_string(str(exc))}
        safe_write_json(out_dir / f"filter_{_norm(label)}.json", result)
        return result

    async def _restore_searched_expanded(self, *, browser: BrowserSession, query: str, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None:
            return {"pass": False, "reason": "no page"}
        await browser.goto_base_and_complete_sso(RULES_URL); await browser.wait_ready()
        surface = await _page_surface(page); search_cid = ""; expand_cid = ""
        search = _search_candidate(surface)
        if query and search and search.get("selector"):
            cap = self.graph.observe_capability(page_family=FAMILY, kind="search", label=str(search.get("label") or search.get("placeholder") or "Search"), selector=str(search.get("selector")), role=str(search.get("role") or ""), placeholder=str(search.get("placeholder") or ""), scope="listing_surface", run_id=run_id, evidence={"url": page.url})
            search_cid = cap["capability_id"]
            loc = page.locator(str(search["selector"])).first
            start = len(browser.network_tab_events)
            await browser.fill_and_log(locator=loc, value=query, selector=str(search["selector"]), action_type="search")
            try: await browser.press_and_log(locator=loc, key="Enter", selector=str(search["selector"]))
            except Exception: pass
            await asyncio.sleep(0.7)
            self.graph.mark_success(search_cid)
            self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir / "search_api", start_index=start, caused_by=search_cid, stage="search_rule")
            surface = await _page_surface(page)
        expand = _expand_candidate(surface, query=query)
        if expand:
            res = await self.discovery._safe_action(browser=browser, family=FAMILY, action=expand, output_dir=out_dir, run_id=run_id, stage="expand_rule")
            if res.get("pass"):
                expand_cid = str(res.get("capability_id") or "")
                surface = res.get("post_surface", {}).get("surface", surface)
        return {"pass": True, "surface": surface, "search_capability_id": search_cid, "expand_capability_id": expand_cid}

    async def _inspect_draft_action(self, *, browser: BrowserSession, action: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        label = _action_label(action); before = await _page_surface(browser.page)
        result = await self.discovery._safe_action(browser=browser, family=FAMILY, action=action, output_dir=out_dir, run_id=run_id, stage=f"inspect_{_norm(label)}")
        if not result.get("pass"):
            return result
        after = result.get("post_surface", {}).get("surface", {})
        contract = _form_contract(after); cid = str(result.get("capability_id") or "")
        safe_write_json(out_dir / f"{_norm(label)}_form_contract.json", contract)
        self.graph.add_relation(source=cid, relation="opens_surface", target=f"state-{contract['structural_fingerprint']}", evidence={"surface": _norm(label), "values_stored": False})
        result.update({"form_contract": contract, "state_delta": _surface_delta(before, after)})
        result["closed"] = await self._close_surface(browser, after)
        safe_write_json(out_dir / f"inspect_{_norm(label)}_summary.json", result)
        return mask_sensitive_data(result)

    async def _probe_mutation_action(self, *, browser: BrowserSession, action: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        label = _action_label(action); selector = str(action.get("selector") or "")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="row_action", label=label, selector=selector, role=str(action.get("role") or ""), scope="entity_row", risk="mutation", run_id=run_id, evidence={"url": page.url, "safe_probe": True})
        cid = cap["capability_id"]; before = await _page_surface(page); start = len(browser.network_tab_events); blocked: List[Dict[str, Any]] = []; installed = False
        async def handler(route: Any, request: Any) -> None:
            method = str(getattr(request, "method", "GET") or "GET").upper(); url = str(getattr(request, "url", "") or "")
            dangerous_get = method == "GET" and any(token in _norm(urlsplit(url).path) for token in MUTATION_TOKENS)
            if method in MUTATING_METHODS or dangerous_get:
                body: Any = getattr(request, "post_data", None)
                if isinstance(body, str):
                    try: body = json.loads(body)
                    except Exception: pass
                blocked.append(mask_sensitive_data({"method": method, "url": url, "payload": body, "payload_shape": _shape(body), "blocked_before_backend": True}))
                await route.abort("blockedbyclient"); return
            await route.continue_()
        click_error = ""
        try:
            await page.route("**/*", handler); installed = True
            browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[label], task_id=f"safe-rule-probe-{_norm(label)}")
            loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
            try: await browser.click_and_wait(action=label, locator=loc, selector=selector)
            except Exception as exc: click_error = mask_sensitive_string(str(exc))
            await asyncio.sleep(0.65); after = await _page_surface(page)
        finally:
            browser.clear_portal_mutation_authorization()
            if installed:
                try: await page.unroute("**/*", handler)
                except Exception:
                    try: await page.unroute("**/*")
                    except Exception: pass
        net = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir, start_index=start, caused_by=cid, stage=f"safe_probe_{_norm(label)}")
        contract = _form_contract(after)
        prerequisites = {"required_controls": [c for c in contract.get("controls", []) if c.get("required")], "confirmation_actions": [a for a in contract.get("actions", []) if _norm(a.get("label")) in {"confirm","deploy","migrate","delete","yes","continue"}], "blocked_network_requests": blocked}
        if blocked or contract.get("dialog_count") or contract.get("control_count"): self.graph.mark_success(cid)
        result = {"pass": bool(blocked or contract.get("dialog_count") or contract.get("control_count")), "capability_id": cid, "label": label, "click_error": click_error, "blocked_before_backend": True, "blocked_requests": blocked, "network": net, "form_contract": contract, "prerequisites": prerequisites, "state_delta": _surface_delta(before, after)}
        result["closed"] = await self._close_surface(browser, after); safe_write_json(out_dir / f"safe_probe_{_norm(label)}.json", result)
        return mask_sensitive_data(result)

    async def _open_create_surface(self, *, browser: BrowserSession, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        await browser.goto_base_and_complete_sso(RULES_URL); await browser.wait_ready(); surface = await _page_surface(page); add = _add_candidate(surface)
        if not add: return {"pass": False, "reason": "Add/Create Rule entry point not found", "surface": surface}
        label = _action_label(add) or "Add"; selector = str(add.get("selector") or "")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="create_entry", label=label, selector=selector, role=str(add.get("role") or ""), scope="listing_surface", risk="draft", run_id=run_id, evidence={"url": page.url, "safe_open_only": True})
        blocked: List[Dict[str, Any]] = []; installed = False
        async def handler(route: Any, request: Any) -> None:
            method = str(getattr(request, "method", "GET") or "GET").upper()
            if method in MUTATING_METHODS:
                blocked.append(mask_sensitive_data({"method": method, "url": str(getattr(request, "url", "") or ""), "payload": getattr(request, "post_data", None), "blocked_before_backend": True})); await route.abort("blockedbyclient"); return
            await route.continue_()
        try:
            await page.route("**/*", handler); installed = True
            browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[label], task_id="safe-open-rule-create")
            loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
            await browser.click_and_wait(action=label, locator=loc, selector=selector); await asyncio.sleep(0.55)
        except Exception as exc:
            return {"pass": False, "capability_id": cap["capability_id"], "error": mask_sensitive_string(str(exc)), "blocked_requests": blocked}
        finally:
            browser.clear_portal_mutation_authorization()
            if installed:
                try: await page.unroute("**/*", handler)
                except Exception:
                    try: await page.unroute("**/*")
                    except Exception: pass
        after = await self.discovery._learn_surface(browser=browser, family=FAMILY, run_id=run_id, output_dir=out_dir / "create_surface", revealed_by=cap["capability_id"])
        self.graph.mark_success(cap["capability_id"])
        return {"pass": True, "capability_id": cap["capability_id"], "blocked_requests": blocked, "surface": after.get("surface", {}), "evidence": after}

    def _promote_form_topology(self, *, graph: Mapping[str, Any], execution: Mapping[str, Any], create_capability_id: str, run_id: str) -> Dict[str, Any]:
        attempts = [a for a in (execution.get("attempts") or []) if isinstance(a, Mapping)]
        node_by_id = {str(n.get("node_id")): n for n in (graph.get("nodes") or []) if isinstance(n, Mapping)}
        cap_by_node: Dict[str, str] = {}
        replay_steps: List[Dict[str, Any]] = [{"type": "navigate", "action": "navigate", "scope": FAMILY, "wait_for": ["rules_listing_ready"]}, {"type": "action", "action": "open_create", "capability_id": create_capability_id, "scope": "create_surface", "wait_for": ["rule_form_ready"]}]
        for a in attempts:
            node_id = str(a.get("node_id") or ""); node = node_by_id.get(node_id, {}); field = str(node.get("field_key") or a.get("field") or "field")
            labels = ((node.get("semantic_locator") or {}).get("labels") or [field]) if isinstance(node.get("semantic_locator"), Mapping) else [field]
            label = str(labels[0] if labels else field)
            cap = self.graph.observe_capability(page_family=FAMILY, kind="form_control", label=label, selector=str(a.get("selector") or ""), scope="form_surface", section=str(node.get("section") or a.get("section") or ""), parent_capability_id=create_capability_id, risk="draft", run_id=run_id, evidence={"input_path": node.get("input_path"), "row_kind": node.get("row_kind"), "row_index": node.get("row_index"), "values_stored": False})
            cid = cap["capability_id"]; cap_by_node[node_id] = cid
            if a.get("success") and a.get("exact_verified"): self.graph.mark_success(cid)
            replay_steps.append({"type": "form_field", "action": str(node.get("action") or ""), "capability_id": cid, "value_source": "current_input", "input_path": str(node.get("input_path") or ""), "section": str(node.get("section") or ""), "row_kind": str(node.get("row_kind") or ""), "row_index": node.get("row_index"), "depends_on": list(node.get("depends_on") or []), "verification": str(node.get("verification") or "exact"), "scope": "form_surface", "wait_for": ["exact_commit", "dependent_child_mount_if_any"]})
        for e in graph.get("dependency_edges") or []:
            if not isinstance(e, Mapping): continue
            src = cap_by_node.get(str(e.get("from") or "")); dst = cap_by_node.get(str(e.get("to") or ""))
            if src and dst: self.graph.add_relation(source=src, relation=str(e.get("relation") or "depends_on"), target=dst, evidence={"parent_value_stored": False})
        profile = self.graph.observe_replay_profile(page_family=FAMILY, name="create_rule", steps=replay_steps, preconditions=["authenticated_shared_browser", "rules_listing_ready", "create_surface_available", "current_input_present"], verified=bool(execution.get("pass")), evidence={"run_id": run_id, "phase": "rule", "mapping_identifier_async_gate": True, "condition_rows_sequential": True, "values_stored": False}, run_id=run_id)
        return {"form_capabilities": cap_by_node, "replay_profile": profile, "verified": bool(profile.get("verified"))}

    async def _exercise_create_form(self, *, browser: BrowserSession, payload: Dict[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        opened = await self._open_create_surface(browser=browser, run_id=run_id, out_dir=out_dir)
        if not opened.get("pass"):
            safe_write_json(out_dir / "create_form_exercise.json", opened); return opened
        vocabulary = await learn_complete_form_vocabulary(page=browser.page, graph=self.graph, page_family=FAMILY, run_id=run_id, output_dir=out_dir / "form_vocabulary")
        graph = apply_dependency_execution_contract(compile_phase_state_graph(payload, PHASE), phase=PHASE); blueprint = _value_free_rule_blueprint(graph); safe_write_json(out_dir / "parent_child_dependency_blueprint.json", blueprint)
        before_controls = await capture_stateful_controls(browser.page, PHASE)
        safe_write_json(out_dir / "controls_before_fill.structural.json", {"controls": [{k: c.get(k) for k in ("label","section","row_kind","row_index","role","type","framework_key","required","disabled","readonly","expanded","selector")} for c in before_controls], "values_stored": False})
        network_start = len(browser.network_tab_events); warnings: List[str] = []
        # Build and fill physical Condition rows with the production-safe Rules helper.
        row_audit = await _apply_rule_condition_row_adds(browser.page, payload, warnings=warnings)
        safe_write_json(out_dir / "condition_row_transaction.structural.json", {k: v for k, v in row_audit.items() if k not in {"row_values", "final_live_rows", "fill_attempts"}} | {"values_stored": False})
        # Run the shared dependency scheduler afterwards to fill/verify every remaining node.
        execution = await execute_phase_state_graph(browser.page, graph, phase=PHASE, max_retries=2)
        value_free_execution = _execution_value_free(execution); safe_write_json(out_dir / "stateful_form_execution.structural.json", value_free_execution)
        live_rows = await _inspect_rule_condition_rows(browser.page); expected_rows = _extract_rule_condition_rows(payload); row_proof = _rule_condition_rows_exact(live_rows, expected_rows)
        validity = await _inspect_rule_create_form_validity(browser.page)
        # Persist only structural validity/reason fields, not current values.
        validity_struct = {k: v for k, v in validity.items() if k not in {"values", "current_values", "raw_values"}}
        safe_write_json(out_dir / "rule_form_validity.structural.json", mask_sensitive_data(validity_struct))
        safe_write_json(out_dir / "condition_exact_proof.structural.json", {"pass": bool(row_proof.get("pass")), "distinct_row_identity_pass": bool(row_proof.get("distinct_row_identity_pass")), "expected_count": len(expected_rows), "actual_count": len(live_rows), "values_stored": False})
        network = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir / "api", start_index=network_start, stage="rule_deep_form_fill")
        after_surface = await _page_surface(browser.page); safe_write_json(out_dir / "filled_surface.structural.json", _form_contract(after_surface))
        verified = bool(execution.get("pass") and row_audit.get("summary", {}).get("exact_input_pass") and row_proof.get("pass"))
        exec_for_promotion = dict(execution); exec_for_promotion["pass"] = verified
        promoted = self._promote_form_topology(graph=graph, execution=exec_for_promotion, create_capability_id=str(opened.get("capability_id") or ""), run_id=run_id)
        closed = await self._close_surface(browser, after_surface)
        result = {"pass": verified, "phase": PHASE, "create_entry_capability_id": opened.get("capability_id"), "dependency_blueprint": blueprint, "condition_transaction": {"summary": row_audit.get("summary") or {}, "warnings": warnings, "values_stored": False}, "execution": value_free_execution, "condition_exact_proof": {"pass": bool(row_proof.get("pass")), "distinct_row_identity_pass": bool(row_proof.get("distinct_row_identity_pass")), "expected_count": len(expected_rows), "actual_count": len(live_rows)}, "form_validity": mask_sensitive_data(validity_struct), "api": network, "promoted": promoted, "closed_without_save": closed, "save_create_submit_executed": False, "values_stored": False}
        safe_write_json(out_dir / "create_form_exercise.json", result); return mask_sensitive_data(result)

    async def run(self, *, ctx: RunContext, input_json: str | Path) -> Dict[str, Any]:
        payload = json.loads(Path(input_json).read_text(encoding="utf-8")); query = _query_for(payload); root = Path(ctx.run_dir); out = root / "deep_discovery" / FAMILY; out.mkdir(parents=True, exist_ok=True)
        agentq = HIPAgentQController(memory_root=Path(self.config.reporting.memory_dir), run_dir=root / "agentq_rule_deep", config=self.config)
        summary: Dict[str, Any] = {"schema_version": "hip.rule-deep-discovery.v1", "run_id": ctx.run_id, "started_at": utc_now(), "page_family": FAMILY, "safe_discovery": True, "mutation_probe_network_abort": True, "input_values_persisted_to_capability_memory": False}
        async with BrowserSession(self.config, root) as browser:
            await agentq.start(); browser.agentq_controller = agentq
            try:
                browser._active_phase_name = "deep_discovery_rules"; browser._current_stage = "deep_discovery:rules:open"
                await browser.goto_base_and_complete_sso(RULES_URL); await browser.wait_ready(); open_start = len(browser.network_tab_events)
                initial = await self.discovery._learn_surface(browser=browser, family=FAMILY, run_id=ctx.run_id, output_dir=out / "initial")
                summary["initial"] = initial; summary["open_network"] = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out, start_index=open_start, stage="deep_open")
                surface = initial.get("surface", {})
                filters = _listing_filter_candidates(surface); summary["listing_filters"] = [await self._inspect_filter(browser=browser, control=c, run_id=ctx.run_id, out_dir=out / "filters") for c in filters[:20]]
                await browser.goto_base_and_complete_sso(RULES_URL); await browser.wait_ready(); surface = await _page_surface(browser.page)
                pagination = _pagination_candidates(surface); summary["pagination_capabilities"] = []
                for p in pagination:
                    label = _action_label(p) or "Page navigation"; cap = self.graph.observe_capability(page_family=FAMILY, kind="pagination", label=label, selector=str(p.get("selector") or ""), role=str(p.get("role") or ""), scope="listing_surface", run_id=ctx.run_id, evidence={"url": surface.get("url")}); summary["pagination_capabilities"].append({"capability_id": cap["capability_id"], "label": label})
                next_action = next((p for p in pagination if "next" in _norm(_action_label(p))), None)
                if next_action:
                    before_page = await _page_surface(browser.page); nxt = await self.discovery._safe_action(browser=browser, family=FAMILY, action=next_action, output_dir=out / "pagination", run_id=ctx.run_id, stage="pagination_next"); summary["pagination_next_validation"] = nxt
                    if nxt.get("pass"):
                        after_page = nxt.get("post_surface", {}).get("surface", {}); summary["pagination_state_delta"] = _surface_delta(before_page, after_page); prev = next((p for p in _pagination_candidates(after_page) if "previous" in _norm(_action_label(p)) or _norm(_action_label(p)) == "prev"), None)
                        if prev: summary["pagination_restore"] = await self.discovery._safe_action(browser=browser, family=FAMILY, action=prev, output_dir=out / "pagination", run_id=ctx.run_id, stage="pagination_previous")
                restored = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "listing"); current = restored.get("surface", {}); expand_cid = str(restored.get("expand_capability_id") or "")
                actions = _row_actions(current, query=query); inventory = []
                for action in actions:
                    label = str(action.get("label") or _action_label(action)); cap = self.graph.observe_capability(page_family=FAMILY, kind="row_action", label=label, selector=str(action.get("selector") or ""), role=str(action.get("role") or ""), scope="entity_row", risk=str(action.get("risk") or classify_risk(label)), parent_capability_id=expand_cid, run_id=ctx.run_id, evidence={"url": current.get("url")})
                    if expand_cid: self.graph.add_relation(source=expand_cid, relation="reveals", target=cap["capability_id"])
                    inventory.append({"capability_id": cap["capability_id"], "label": label, "risk": cap.get("risk")})
                summary["searched"] = bool(query and restored.get("search_capability_id")); summary["expanded"] = bool(expand_cid); summary["row_action_inventory"] = inventory
                action_results: Dict[str, Any] = {}
                for token in DRAFT_INSPECT_ACTIONS:
                    restored2 = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "restore_between_actions")
                    candidate = next((a for a in _row_actions(restored2.get("surface", {}), query=query) if token in _norm(a.get("label"))), None)
                    if not candidate: continue
                    label = str(candidate.get("label") or token)
                    if _norm(label) in {_norm(x) for x in action_results}: continue
                    action_results[label] = await self._inspect_draft_action(browser=browser, action=candidate, run_id=ctx.run_id, out_dir=out / "draft_actions" / _norm(label))
                summary["draft_action_learning"] = action_results
                mutation_results: Dict[str, Any] = {}
                for token in MUTATION_PROBE_ACTIONS:
                    restored3 = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "restore_between_mutation_probes")
                    candidate = next((a for a in _row_actions(restored3.get("surface", {}), query=query) if token in _norm(a.get("label"))), None)
                    if candidate: mutation_results[token] = await self._probe_mutation_action(browser=browser, action=candidate, run_id=ctx.run_id, out_dir=out / "mutation_probes" / token)
                summary["mutation_prerequisite_learning"] = mutation_results
                summary["create_form_parent_child_learning"] = await self._exercise_create_form(browser=browser, payload=payload, run_id=ctx.run_id, out_dir=out / "create_form")
            finally:
                try: await agentq.close()
                except Exception: pass
        self.graph.save(); summary["capability_graph_manifest"] = self.graph.manifest(); summary["completed_at"] = utc_now(); safe_write_json(root / "rules_deep_discovery_summary.json", mask_sensitive_data(summary)); return mask_sensitive_data(summary)
