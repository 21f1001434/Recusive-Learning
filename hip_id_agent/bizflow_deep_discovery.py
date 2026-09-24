from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .agentq_runtime import HIPAgentQController
from .autonomous_dependency_runtime import apply_dependency_execution_contract
from .bizflow_kb import (
    _ensure_bizflows_listing_page,
    _find_bizflow_add_button,
    _click_bizflow_template_link_after_add,
    _wait_for_bizflow_form_surface,
    capture_and_fill_bizflow_multitab_form,
)
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
    _event_dict,
    _expand_candidate,
    _norm,
    _page_surface,
    _safe_close_candidates,
    _search_candidate,
    _shape,
)
from .safe_io import safe_write_json
from .phase_vocabulary_learning import learn_complete_form_vocabulary
from .security import mask_sensitive_data, mask_sensitive_string
from .stateful_form_runtime import compile_phase_state_graph
from .transport_profile_deep_discovery import _action_api_causal_trace


BIZFLOW_URL = PHASE_URLS["biz_flow"]
FAMILY = "bizflows"
PHASE = "biz_flow"
MUTATION_PROBE_ACTIONS = ("migrate", "deploy", "delete")


def _bizflow_obj(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    objects = payload.get("objects") if isinstance(payload.get("objects"), Mapping) else {}
    obj = objects.get("biz_flow") if isinstance(objects.get("biz_flow"), Mapping) else {}
    return obj


def _query_for(payload: Mapping[str, Any]) -> str:
    obj = _bizflow_obj(payload)
    details = obj.get("flow_details") if isinstance(obj.get("flow_details"), Mapping) else {}
    return str(details.get("business_flow_name") or obj.get("flow_name") or obj.get("name") or "").strip()


def _field_label(node: Mapping[str, Any]) -> str:
    loc = node.get("semantic_locator") if isinstance(node.get("semantic_locator"), Mapping) else {}
    labels = [str(x) for x in (loc.get("labels") or []) if str(x).strip()]
    placeholders = [str(x) for x in (loc.get("placeholders") or []) if str(x).strip()]
    return (labels or placeholders or [str(node.get("field_key") or "BizFlow field")])[0]


def _value_free_bizflow_blueprint(graph: Mapping[str, Any]) -> Dict[str, Any]:
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
                "row_kind": loc.get("row_kind"),
                "row_index": loc.get("row_index"),
            },
            "expected_value_stored": False,
        })
    edges: List[Dict[str, Any]] = []
    for raw in graph.get("dependency_edges") or []:
        if not isinstance(raw, Mapping):
            continue
        edges.append({
            "edge_id": raw.get("edge_id"), "from": raw.get("from"), "to": raw.get("to"),
            "relation": raw.get("relation"), "parent_value_stored": False,
        })
    return {
        "schema_version": "hip.bizflow-dependency-blueprint.v1",
        "phase": PHASE,
        "object_family": "biz_flow",
        "nodes": nodes,
        "dependency_edges": edges,
        "repeatable_rows": dict(graph.get("repeatable_rows") or {}),
        "section_order": ["Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing"],
        "critical_gates": [
            "Source Type exact before Source Application",
            "Source Application exact before Source Transport Profile / Source Document Type",
            "Flow Identifier Document Type exact before Attribute Name and Operator/Value",
            "Target Type exact before Target Application",
            "Target Application exact before Target Transport Profile / Target Document Type",
            "Process Step Type exact before step-specific Mapping Transformer / Enricher children",
            "Target File Name Config exact before separator/extension/file-name-part rows",
            "Each physical repeatable row must be exact before the next Add",
            "Routing Condition Type exact before Attribute/Operator/Value",
            "Routing Action Type exact before routing Target",
        ],
        "input_accounting": mask_sensitive_data(list(graph.get("input_accounting") or [])),
        "values_stored": False,
    }


def _execution_summary_value_free(execution: Mapping[str, Any]) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for raw in execution.get("attempts") or []:
        if not isinstance(raw, Mapping):
            continue
        attempts.append({
            "node_id": raw.get("node_id"), "field": raw.get("field") or raw.get("field_key"),
            "input_path": raw.get("input_path"), "section": raw.get("section") or raw.get("bizflow_tab"),
            "row_kind": raw.get("row_kind"), "row_index": raw.get("row_index"),
            "success": bool(raw.get("success")), "exact_verified": bool(raw.get("exact_verified")),
            "selector": raw.get("selector"), "dependencies": list(raw.get("dependencies") or []),
            "reason": raw.get("reason"), "values_stored": False,
        })
    sections = []
    for raw in execution.get("section_executions") or []:
        if isinstance(raw, Mapping):
            sections.append({
                "section": raw.get("graph_section") or raw.get("section") or raw.get("tab"),
                "pass": bool(raw.get("pass")), "status": raw.get("status"),
                "failed_count": len(raw.get("failed_attempts") or []), "values_stored": False,
            })
    return {
        "pass": bool(execution.get("pass")), "status": execution.get("status"),
        "graph_id": execution.get("graph_id"), "attempts": attempts,
        "failed_count": len(execution.get("failed_attempts") or []),
        "observed_dependency_edges": [
            {"from": e.get("from"), "to": e.get("to"), "relation": e.get("relation"), "parent_value_stored": False}
            for e in (execution.get("observed_dependency_edges") or []) if isinstance(e, Mapping)
        ],
        "section_executions": sections,
        "values_stored": False,
    }


class BizFlowDeepDiscoveryFlow:
    """Deep-learn BizFlow listing/actions and the full unsaved multi-tab BizFlow form.

    This deliberately reuses capture_and_fill_bizflow_multitab_form so the learned
    capability topology is identical to the production BizFlow runtime: target-first
    section filling, repeatable physical rows, process-step accordions, routing +Add,
    section judges, dependency-state repair and no Save/Create/Submit.
    """

    def __init__(self, config: AppConfig, *, capability_graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        graph_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = capability_graph or HIPCapabilityGraph(graph_root)
        self.discovery = HIPPortalDiscoveryFlow(config, capability_graph=self.graph)

    async def _close_surface(self, browser: BrowserSession, surface: Mapping[str, Any]) -> bool:
        page = browser.page
        if page is None: return False
        for close in _safe_close_candidates(surface):
            selector = str(close.get("selector") or ""); label = _action_label(close) or "Close"
            try:
                loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
                await browser.click_and_wait(action="close BizFlow discovery surface", locator=loc, selector=selector)
                await asyncio.sleep(0.3); return True
            except Exception: continue
        try:
            await page.keyboard.press("Escape"); await asyncio.sleep(0.2); return True
        except Exception: return False

    async def _inspect_filter(self, *, browser: BrowserSession, control: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        selector = str(control.get("selector") or ""); label = str(control.get("label") or control.get("placeholder") or "Filter")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="filter", label=label, selector=selector,
            role=str(control.get("role") or ""), placeholder=str(control.get("placeholder") or ""), scope="listing_surface", run_id=run_id, evidence={"url": page.url})
        before = await _page_surface(page); start = len(browser.network_tab_events)
        try:
            await browser.click_and_wait(action=f"inspect BizFlow filter {label}", locator=page.locator(selector).first, selector=selector)
            await asyncio.sleep(0.3)
            options = await page.evaluate(r"""() => [...document.querySelectorAll('[role=option],.dds__dropdown__item-option')].filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).slice(0,1000).map(el=>String(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean)""")
            try: await page.keyboard.press("Escape")
            except Exception: pass
            after = await _page_surface(page); self.graph.mark_success(cap["capability_id"])
            net = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir, start_index=start, caused_by=cap["capability_id"], stage="inspect_filter")
            result = {"pass": True, "capability_id": cap["capability_id"], "option_labels": mask_sensitive_data(options or []), "network": net, "state_delta": _surface_delta(before, after), "selection_changed": False}
        except Exception as exc:
            result = {"pass": False, "capability_id": cap["capability_id"], "error": mask_sensitive_string(str(exc))}
        safe_write_json(out_dir / f"filter_{_norm(label)}.json", result); return result

    async def _restore_searched_expanded(self, *, browser: BrowserSession, query: str, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        await browser.goto_base_and_complete_sso(BIZFLOW_URL); await browser.wait_ready(); await _ensure_bizflows_listing_page(page, BIZFLOW_URL)
        surface = await _page_surface(page); search_cid = ""; expand_cid = ""
        search = _search_candidate(surface)
        if query and search and search.get("selector"):
            cap = self.graph.observe_capability(page_family=FAMILY, kind="search", label=str(search.get("label") or search.get("placeholder") or "Search"), selector=str(search.get("selector")), role=str(search.get("role") or ""), placeholder=str(search.get("placeholder") or ""), scope="listing_surface", run_id=run_id, evidence={"url": page.url})
            search_cid = cap["capability_id"]; loc = page.locator(str(search["selector"])).first; start = len(browser.network_tab_events)
            await browser.fill_and_log(locator=loc, value=query, selector=str(search["selector"]), action_type="search")
            try: await browser.press_and_log(locator=loc, key="Enter", selector=str(search["selector"]))
            except Exception: pass
            await asyncio.sleep(0.8); self.graph.mark_success(search_cid)
            self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir / "search_api", start_index=start, caused_by=search_cid, stage="search_bizflow")
            surface = await _page_surface(page)
        expand = _expand_candidate(surface, query=query)
        if expand:
            res = await self.discovery._safe_action(browser=browser, family=FAMILY, action=expand, output_dir=out_dir, run_id=run_id, stage="expand_bizflow")
            if res.get("pass"):
                expand_cid = str(res.get("capability_id") or ""); surface = res.get("post_surface", {}).get("surface", surface)
        return {"pass": True, "surface": surface, "search_capability_id": search_cid, "expand_capability_id": expand_cid}

    async def _inspect_draft_action(self, *, browser: BrowserSession, action: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        label = _action_label(action); before = await _page_surface(browser.page)
        result = await self.discovery._safe_action(browser=browser, family=FAMILY, action=action, output_dir=out_dir, run_id=run_id, stage=f"inspect_{_norm(label)}")
        if not result.get("pass"): return result
        after = result.get("post_surface", {}).get("surface", {}); contract = _form_contract(after); cid = str(result.get("capability_id") or "")
        safe_write_json(out_dir / f"{_norm(label)}_form_contract.json", contract)
        self.graph.add_relation(source=cid, relation="opens_surface", target=f"state-{contract['structural_fingerprint']}", evidence={"surface": _norm(label), "values_stored": False})
        result.update({"form_contract": contract, "state_delta": _surface_delta(before, after), "closed": await self._close_surface(browser, after)})
        safe_write_json(out_dir / f"inspect_{_norm(label)}_summary.json", result); return mask_sensitive_data(result)

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
        click_error = ""; after: Dict[str, Any] = {}
        try:
            await page.route("**/*", handler); installed = True
            browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[label], task_id=f"safe-bizflow-probe-{_norm(label)}")
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
        contract = _form_contract(after); self.graph.add_relation(source=cid, relation="opens_confirmation_surface", target=f"state-{contract['structural_fingerprint']}", evidence={"blocked_before_backend": True})
        result = {"pass": bool(after), "capability_id": cid, "label": label, "click_error": click_error, "blocked_requests": blocked, "network": net, "form_contract": contract, "state_delta": _surface_delta(before, after), "mutation_delivered": False, "closed": await self._close_surface(browser, after)}
        safe_write_json(out_dir / f"safe_probe_{_norm(label)}.json", result); return mask_sensitive_data(result)

    async def _open_create_surface(self, *, browser: BrowserSession, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        await browser.goto_base_and_complete_sso(BIZFLOW_URL); await browser.wait_ready(); nav = await _ensure_bizflows_listing_page(page, BIZFLOW_URL)
        add = await _find_bizflow_add_button(page)
        if add is None: return {"pass": False, "reason": "Add BizFlow entry point not found", "navigation": nav}
        cap = self.graph.observe_capability(page_family=FAMILY, kind="create_entry", label="Add", selector="+ Add BizFlow", role="button", scope="listing_surface", risk="draft", run_id=run_id, evidence={"url": page.url, "safe_open_only": True})
        blocked: List[Dict[str, Any]] = []; installed = False
        async def handler(route: Any, request: Any) -> None:
            method = str(getattr(request, "method", "GET") or "GET").upper(); url = str(getattr(request, "url", "") or "")
            if method in MUTATING_METHODS:
                body = getattr(request, "post_data", None); blocked.append(mask_sensitive_data({"method": method, "url": url, "payload": body, "blocked_before_backend": True})); await route.abort("blockedbyclient"); return
            await route.continue_()
        try:
            await page.route("**/*", handler); installed = True
            browser.set_portal_mutation_authorization(enabled=True, allowed_labels=["Add", "+ Add", "Create Biz Flow"], task_id="safe-open-bizflow-create")
            await browser.click_and_wait(action="Add", locator=add, selector="+ Add BizFlow"); await asyncio.sleep(0.6)
            template_audit = await _click_bizflow_template_link_after_add(page)
            form_visible = await _wait_for_bizflow_form_surface(page, timeout_ms=9000)
            if not form_visible:
                return {"pass": False, "capability_id": cap["capability_id"], "error": "Create Biz Flow form did not open", "template_audit": template_audit, "blocked_requests": blocked}
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
        attempts_by_node = {str(a.get("node_id")): a for a in (execution.get("attempts") or []) if isinstance(a, Mapping) and a.get("node_id")}
        cap_by_node: Dict[str, str] = {}
        replay_steps: List[Dict[str, Any]] = [
            {"type": "navigate", "action": "open_bizflows", "wait_for": ["listing_ready"]},
            {"type": "action", "action": "open_create", "capability_id": create_capability_id, "scope": "listing_surface", "wait_for": ["template_picker_or_form"]},
            {"type": "action", "action": "choose_b2b_pubsub_template", "scope": "template_picker", "wait_for": ["bizflow_form_ready"]},
        ]
        for node in graph.get("nodes") or []:
            if not isinstance(node, Mapping): continue
            attempt = attempts_by_node.get(str(node.get("node_id") or ""), {}); selector = str(attempt.get("selector") or ""); label = _field_label(node)
            cap = self.graph.observe_capability(page_family=FAMILY, kind="form_field", label=label, selector=selector, scope=str(node.get("row_kind") or "form_surface"), section=str(node.get("section") or ""), risk="draft", run_id=run_id, evidence={"phase": PHASE, "node_id": node.get("node_id"), "field_key": node.get("field_key"), "input_path": node.get("input_path"), "action": node.get("action"), "row_kind": node.get("row_kind"), "row_index": node.get("row_index"), "exact_verified": bool(attempt.get("exact_verified")), "values_stored": False})
            if attempt.get("success"): self.graph.mark_success(cap["capability_id"])
            cap_by_node[str(node.get("node_id"))] = cap["capability_id"]
            replay_steps.append({"type": "form_field", "action": str(node.get("action") or ""), "capability_id": cap["capability_id"], "value_source": str(node.get("input_path") or ""), "scope": str(node.get("row_kind") or "form_surface"), "input_path": str(node.get("input_path") or ""), "section": str(node.get("section") or ""), "row_kind": str(node.get("row_kind") or ""), "row_index": node.get("row_index"), "depends_on": [str(x) for x in (node.get("depends_on") or [])], "verification": str(node.get("verification") or "exact_committed_control_value"), "wait_for": ["exact_commit", "physical_row_identity", "child_mount_if_any", "angular_settle"]})
        for node in graph.get("nodes") or []:
            if not isinstance(node, Mapping): continue
            target = cap_by_node.get(str(node.get("node_id") or ""))
            if not target: continue
            for parent in node.get("depends_on") or []:
                source = cap_by_node.get(str(parent))
                if source: self.graph.add_relation(source=source, relation="parent_before_child", target=target, evidence={"phase": PHASE, "values_stored": False})
        profile = self.graph.observe_replay_profile(page_family=FAMILY, name="create_biz_flow", steps=replay_steps,
            preconditions=["authenticated_shared_browser", "bizflows_listing_ready", "create_surface_available", "current_input_present"],
            verified=bool(execution.get("pass")), evidence={"run_id": run_id, "phase": PHASE, "repeatable_policy": "physical_row_exact_before_next_add", "all_exact_verified": bool(execution.get("pass")), "values_stored": False}, run_id=run_id)
        return {"form_capabilities": cap_by_node, "replay_profile": profile, "verified": bool(profile.get("verified"))}

    async def _exercise_create_form(self, *, browser: BrowserSession, payload: Dict[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        opened = await self._open_create_surface(browser=browser, run_id=run_id, out_dir=out_dir)
        if not opened.get("pass"):
            safe_write_json(out_dir / "create_form_exercise.json", opened); return opened
        vocabulary = await learn_complete_form_vocabulary(page=browser.page, graph=self.graph, page_family=FAMILY, run_id=run_id, output_dir=out_dir / "form_vocabulary")
        graph = apply_dependency_execution_contract(compile_phase_state_graph(payload, PHASE), phase=PHASE)
        blueprint = _value_free_bizflow_blueprint(graph); safe_write_json(out_dir / "parent_child_dependency_blueprint.json", blueprint)
        action_start = len(browser.action_events); network_start = len(browser.network_tab_events)
        # Already on form: capture_and_fill will detect it and skip template re-open.
        form_kb = await capture_and_fill_bizflow_multitab_form(browser.page, payload, fill_dummy=True, judge_output_dir=out_dir / "form_kb", config=self.config)
        execution = form_kb.get("stateful_target_branch_execution") if isinstance(form_kb.get("stateful_target_branch_execution"), Mapping) else {}
        value_free_execution = _execution_summary_value_free(execution); safe_write_json(out_dir / "stateful_form_execution.structural.json", value_free_execution)
        repeatable = form_kb.get("repeatable_row_audit") if isinstance(form_kb.get("repeatable_row_audit"), list) else []
        safe_write_json(out_dir / "repeatable_row_audit.structural.json", mask_sensitive_data([{
            "section": r.get("section") or r.get("tab"), "row_kind": r.get("row_kind"), "requested_count": r.get("requested_count") or r.get("expected_count"), "final_count": r.get("final_count") or r.get("actual_count"), "pass": bool(r.get("pass") or r.get("exact_input_pass")), "values_stored": False,
        } for r in repeatable if isinstance(r, Mapping)]))
        network = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir / "api", start_index=network_start, stage="biz_flow_deep_form_fill")
        causal_trace = _action_api_causal_trace(browser.action_events[action_start:], network.get("transactions") or []); safe_write_json(out_dir / "api" / "ui_api_causal_trace.json", causal_trace)
        after_surface = await _page_surface(browser.page); safe_write_json(out_dir / "filled_surface.structural.json", _form_contract(after_surface))
        promoted = self._promote_form_topology(graph=graph, execution=execution, create_capability_id=str(opened.get("capability_id") or ""), run_id=run_id)
        closed = await self._close_surface(browser, after_surface)
        result = {"pass": bool(execution.get("pass")), "phase": PHASE, "create_entry_capability_id": opened.get("capability_id"), "dependency_blueprint": blueprint, "execution": value_free_execution, "api": network, "ui_api_causal_trace": causal_trace, "promoted": promoted, "closed_without_save": closed, "save_create_submit_executed": False, "values_stored": False}
        safe_write_json(out_dir / "create_form_exercise.json", result); return mask_sensitive_data(result)

    async def run(self, *, ctx: RunContext, input_json: str | Path) -> Dict[str, Any]:
        payload = json.loads(Path(input_json).read_text(encoding="utf-8")); query = _query_for(payload); root = Path(ctx.run_dir); out = root / "deep_discovery" / FAMILY; out.mkdir(parents=True, exist_ok=True)
        agentq = HIPAgentQController(memory_root=Path(self.config.reporting.memory_dir), run_dir=root / "agentq_bizflow_deep", config=self.config)
        summary: Dict[str, Any] = {"schema_version": "hip.bizflow-deep-discovery.v1", "run_id": ctx.run_id, "started_at": utc_now(), "page_family": FAMILY, "safe_discovery": True, "mutation_probe_network_abort": True, "input_values_persisted_to_capability_memory": False}
        async with BrowserSession(self.config, root) as browser:
            await agentq.start(); browser.agentq_controller = agentq
            try:
                browser._active_phase_name = "deep_discovery_bizflows"; browser._current_stage = "deep_discovery:bizflows:open"
                await browser.goto_base_and_complete_sso(BIZFLOW_URL); await browser.wait_ready(); nav = await _ensure_bizflows_listing_page(browser.page, BIZFLOW_URL); summary["navigation"] = nav
                open_start = len(browser.network_tab_events); initial = await self.discovery._learn_surface(browser=browser, family=FAMILY, run_id=ctx.run_id, output_dir=out / "initial")
                summary["initial"] = initial; summary["open_network"] = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out, start_index=open_start, stage="deep_open"); surface = initial.get("surface", {})
                filters = _listing_filter_candidates(surface); summary["listing_filters"] = [await self._inspect_filter(browser=browser, control=c, run_id=ctx.run_id, out_dir=out / "filters") for c in filters[:20]]
                await browser.goto_base_and_complete_sso(BIZFLOW_URL); await browser.wait_ready(); await _ensure_bizflows_listing_page(browser.page, BIZFLOW_URL); surface = await _page_surface(browser.page)
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
        self.graph.save(); summary["capability_graph_manifest"] = self.graph.manifest(); summary["completed_at"] = utc_now(); safe_write_json(root / "bizflow_deep_discovery_summary.json", mask_sensitive_data(summary)); return mask_sensitive_data(summary)
