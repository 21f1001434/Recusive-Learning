from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .agentq_runtime import HIPAgentQController
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .dummy_fill_e2e import PHASE_URLS
from .maximum_observability import MaximumObservabilityCollector
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
from .security import mask_sensitive_data, mask_sensitive_string


DATAMAP_URL = PHASE_URLS["data_map"]
FAMILY = "data_maps"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MUTATION_TOKENS = {"deploy", "delete", "remove", "migrate", "publish", "enable", "disable"}
DRAFT_INSPECT_ACTIONS = ("edit", "clone", "copy", "view", "details", "detail", "history", "audit")
MUTATION_PROBE_ACTIONS = ("migrate", "deploy", "delete")


def _fingerprint(value: Any) -> str:
    raw = json.dumps(mask_sensitive_data(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _structural_surface(surface: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a value-free structural signature of a portal state."""
    controls = []
    for c in surface.get("controls", []) or []:
        if not isinstance(c, Mapping):
            continue
        controls.append({
            "tag": c.get("tag"), "role": c.get("role"), "type": c.get("type"),
            "label": c.get("label"), "placeholder": c.get("placeholder"),
            "formControlName": c.get("formControlName"), "selector": c.get("selector"),
            "expanded": c.get("expanded"), "owns": c.get("owns"), "controls": c.get("controls"),
            "disabled": bool(c.get("disabled")), "required": bool(c.get("required")),
            "readOnly": bool(c.get("readOnly")), "ariaInvalid": c.get("ariaInvalid"),
        })
    actions = []
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping):
            continue
        actions.append({
            "role": a.get("role"), "label": _action_label(a), "selector": a.get("selector"),
            "expanded": a.get("expanded"), "disabled": bool(a.get("disabled")),
            "href_path": urlsplit(str(a.get("href") or "")).path if a.get("href") else "",
        })
    dialogs = []
    for d in surface.get("dialogs", []) or []:
        if not isinstance(d, Mapping):
            continue
        dialogs.append({"tag": d.get("tag"), "role": d.get("role"), "classes": d.get("classes"), "controlCount": d.get("controlCount")})
    return {"url_path": urlsplit(str(surface.get("url") or "")).path, "title": surface.get("title"), "controls": controls, "actions": actions, "dialogs": dialogs}


def _surface_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    b = _structural_surface(before); a = _structural_surface(after)
    def keys(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> set[str]:
        return {"|".join(str(r.get(f) or "") for f in fields) for r in rows}
    bc = keys(b.get("controls", []), ("label", "formControlName", "role", "selector"))
    ac = keys(a.get("controls", []), ("label", "formControlName", "role", "selector"))
    ba = keys(b.get("actions", []), ("label", "role", "selector"))
    aa = keys(a.get("actions", []), ("label", "role", "selector"))
    return {
        "before_fingerprint": _fingerprint(b), "after_fingerprint": _fingerprint(a),
        "changed": _fingerprint(b) != _fingerprint(a),
        "controls_added": sorted(ac - bc)[:300], "controls_removed": sorted(bc - ac)[:300],
        "actions_added": sorted(aa - ba)[:300], "actions_removed": sorted(ba - aa)[:300],
        "dialog_count_before": len(b.get("dialogs", [])), "dialog_count_after": len(a.get("dialogs", [])),
    }


def _pagination_candidates(surface: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[tuple[int, Dict[str, Any]]] = []
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping) or a.get("disabled") or not a.get("selector"):
            continue
        label = _action_label(a)
        n = _norm(label)
        score = 0
        if n in {"next", "next_page", "previous", "previous_page", "prev", "first", "last"}: score += 10
        if "page" in n and any(x in n for x in ("next", "previous", "prev", "first", "last")): score += 8
        if re.fullmatch(r"page_?\d+", n): score += 6
        if label.strip().isdigit(): score += 4
        title = str(a.get("title") or "").lower(); aria = str(a.get("ariaLabel") or "").lower()
        if "next" in title or "next" in aria: score += 8
        if "previous" in title or "previous" in aria: score += 8
        if score:
            rows.append((score, dict(a)))
    rows.sort(key=lambda x: -x[0])
    return [r for _, r in rows]


def _listing_filter_candidates(surface: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[tuple[int, Dict[str, Any]]] = []
    for c in surface.get("controls", []) or []:
        if not isinstance(c, Mapping) or c.get("disabled"):
            continue
        hay = " ".join(str(c.get(k) or "") for k in ("label", "placeholder", "formControlName", "role")).lower()
        score = 0
        if any(x in hay for x in ("filter", "status", "environment", "type", "sort")): score += 6
        if str(c.get("role") or "") == "combobox": score += 2
        if score and c.get("selector"):
            out.append((score, dict(c)))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out]


def _row_actions(surface: Mapping[str, Any], query: str = "") -> List[Dict[str, Any]]:
    q = str(query or "").lower()
    out: List[Dict[str, Any]] = []
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping) or a.get("disabled"):
            continue
        label = _action_label(a)
        n = _norm(label)
        if not label or not any(token in n for token in ("edit", "clone", "copy", "migrate", "deploy", "delete", "view", "detail", "history", "audit")):
            continue
        scope = str(a.get("scope") or "")
        if q and scope and q not in scope.lower():
            continue
        row = dict(a); row["label"] = label; row["risk"] = classify_risk(label)
        out.append(row)
    # Stable semantic ordering, not DOM order.
    priority = {"edit": 0, "clone": 1, "copy": 1, "view": 2, "details": 2, "history": 3, "audit": 3, "migrate": 10, "deploy": 11, "delete": 12}
    def p(row: Mapping[str, Any]) -> tuple[int, str]:
        n = _norm(row.get("label")); rank = min((v for k, v in priority.items() if k in n), default=5)
        return rank, n
    return sorted(out, key=p)


def _form_contract(surface: Mapping[str, Any]) -> Dict[str, Any]:
    controls = []
    for c in surface.get("controls", []) or []:
        if not isinstance(c, Mapping): continue
        controls.append({
            "label": c.get("label"), "tag": c.get("tag"), "role": c.get("role"), "type": c.get("type"),
            "form_control_name": c.get("formControlName"), "selector": c.get("selector"),
            "required": bool(c.get("required")), "disabled": bool(c.get("disabled")), "read_only": bool(c.get("readOnly")),
            "parent_owns": c.get("owns"), "parent_controls": c.get("controls"),
        })
    actions = []
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping): continue
        label = _action_label(a)
        if label:
            actions.append({"label": label, "role": a.get("role"), "selector": a.get("selector"), "risk": classify_risk(label), "disabled": bool(a.get("disabled"))})
    required = [c for c in controls if c.get("required")]
    return {
        "structural_fingerprint": _fingerprint({"controls": controls, "actions": actions, "dialogs": surface.get("dialogs", [])}),
        "control_count": len(controls), "required_control_count": len(required), "controls": controls,
        "actions": actions, "dialog_count": len(surface.get("dialogs", []) or []), "values_stored": False,
    }


class DataMapDeepDiscoveryFlow:
    """Systematically learn the Data Maps portal family without mutating Dell.

    Safe draft/read actions are opened and closed. Mutation-grade actions are
    probed only behind a hard Playwright network-abort barrier so their dialog,
    prerequisites and request payload can be learned without backend delivery.
    """

    def __init__(self, config: AppConfig, *, capability_graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        graph_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = capability_graph or HIPCapabilityGraph(graph_root)
        self.discovery = HIPPortalDiscoveryFlow(config, capability_graph=self.graph)

    async def _agentq_transition(self, *, agentq: HIPAgentQController, browser: BrowserSession, label: str, before: Mapping[str, Any], after: Mapping[str, Any], success: bool, capability_id: str = "") -> Dict[str, Any]:
        page = browser.page
        if page is None: return {}
        node = {"node_id": capability_id or _norm(label), "field_key": _norm(label), "section": "Data Maps listing", "expected_value": "<structural action>"}
        rep_before = await agentq.represent(page=page, phase="data_map_discovery", controls=list(before.get("controls", []) or []), surface_gate={"safe_discovery": True})
        plan = {"selected_action": "click", "capability_id": capability_id, "label": label, "source": "capability_graph"}
        outcome = {"success": bool(success), "state_delta": _surface_delta(before, after)}
        return await agentq.record_outcome(page=page, phase="data_map_discovery", node=node, representation_before=rep_before, action_plan=plan, outcome=outcome, controls_after=list(after.get("controls", []) or []), surface_gate={"safe_discovery": True})

    async def _close_surface(self, browser: BrowserSession, surface: Mapping[str, Any]) -> bool:
        page = browser.page
        if page is None: return False
        for close in _safe_close_candidates(surface):
            selector = str(close.get("selector") or "")
            label = _action_label(close) or "Close"
            try:
                loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
                await browser.click_and_wait(action="close deep discovery surface", locator=loc, selector=selector)
                await asyncio.sleep(0.4)
                return True
            except Exception:
                continue
        # Escape is safe only as a last-resort surface dismissal.
        try:
            await page.keyboard.press("Escape"); await asyncio.sleep(0.3); return True
        except Exception:
            return False

    async def _restore_searched_expanded(self, *, browser: BrowserSession, query: str, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        await browser.goto_base_and_complete_sso(DATAMAP_URL); await browser.wait_ready()
        surface = await _page_surface(page)
        search = _search_candidate(surface)
        search_cid = ""; expand_cid = ""
        if query and search and search.get("selector"):
            cap = self.graph.observe_capability(page_family=FAMILY, kind="search", label=str(search.get("label") or search.get("placeholder") or "Search"), selector=str(search.get("selector")), role=str(search.get("role") or ""), placeholder=str(search.get("placeholder") or ""), scope="listing_surface", run_id=run_id, evidence={"url": page.url})
            search_cid = cap["capability_id"]
            loc = page.locator(str(search["selector"])).first
            await browser.fill_and_log(locator=loc, value=query, selector=str(search["selector"]), action_type="search")
            try: await browser.press_and_log(locator=loc, key="Enter", selector=str(search["selector"]))
            except Exception: pass
            await asyncio.sleep(0.7)
            self.graph.mark_success(search_cid)
            surface = await _page_surface(page)
        expand = _expand_candidate(surface, query=query)
        if expand:
            res = await self.discovery._safe_action(browser=browser, family=FAMILY, action=expand, output_dir=out_dir, run_id=run_id, stage="restore_expand")
            if res.get("pass"):
                expand_cid = str(res.get("capability_id") or "")
                surface = res.get("post_surface", {}).get("surface", surface)
        return {"pass": True, "surface": surface, "search_capability_id": search_cid, "expand_capability_id": expand_cid}

    async def _inspect_filter(self, *, browser: BrowserSession, control: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False}
        selector = str(control.get("selector") or "")
        label = str(control.get("label") or control.get("placeholder") or "Filter")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="filter", label=label, selector=selector, role=str(control.get("role") or ""), placeholder=str(control.get("placeholder") or ""), scope="listing_surface", run_id=run_id, evidence={"url": page.url})
        before = await _page_surface(page); start = len(browser.network_tab_events)
        try:
            loc = page.locator(selector).first
            await browser.click_and_wait(action=f"inspect filter {label}", locator=loc, selector=selector)
            await asyncio.sleep(0.35)
            # Capture only option labels; never select a filter during discovery.
            options = await page.evaluate(r"""() => [...document.querySelectorAll('[role="option"],.dds__dropdown__item-option')].filter(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).slice(0,1000).map(el=>String(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean)""")
            try: await page.keyboard.press("Escape")
            except Exception: pass
            await asyncio.sleep(0.2)
            after = await _page_surface(page)
            self.graph.mark_success(cap["capability_id"])
            net = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir, start_index=start, caused_by=cap["capability_id"], stage="inspect_filter")
            result = {"pass": True, "capability_id": cap["capability_id"], "option_labels": mask_sensitive_data(options or []), "network": net, "state_delta": _surface_delta(before, after)}
        except Exception as exc:
            result = {"pass": False, "capability_id": cap["capability_id"], "error": mask_sensitive_string(str(exc))}
        safe_write_json(out_dir / f"filter_{_norm(label)}.json", result)
        return result

    async def _inspect_draft_action(self, *, browser: BrowserSession, agentq: HIPAgentQController, action: Mapping[str, Any], query: str, run_id: str, out_dir: Path) -> Dict[str, Any]:
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        label = _action_label(action)
        before = await _page_surface(page)
        result = await self.discovery._safe_action(browser=browser, family=FAMILY, action=action, output_dir=out_dir, run_id=run_id, stage=f"inspect_{_norm(label)}")
        if not result.get("pass"):
            return result
        after = result.get("post_surface", {}).get("surface", {})
        contract = _form_contract(after)
        safe_write_json(out_dir / f"{_norm(label)}_form_contract.json", contract)
        cid = str(result.get("capability_id") or "")
        self.graph.add_relation(source=cid, relation="opens_surface", target=f"state-{contract['structural_fingerprint']}", evidence={"surface": _norm(label), "values_stored": False})
        result["form_contract"] = contract
        result["state_delta"] = _surface_delta(before, after)
        result["agentq"] = await self._agentq_transition(agentq=agentq, browser=browser, label=label, before=before, after=after, success=True, capability_id=cid)
        result["closed"] = await self._close_surface(browser, after)
        safe_write_json(out_dir / f"inspect_{_norm(label)}_summary.json", result)
        return mask_sensitive_data(result)

    async def _probe_mutation_action(self, *, browser: BrowserSession, agentq: HIPAgentQController, action: Mapping[str, Any], run_id: str, out_dir: Path) -> Dict[str, Any]:
        """Probe a dangerous action while aborting any possible mutation request."""
        page = browser.page
        if page is None: return {"pass": False, "reason": "no page"}
        label = _action_label(action)
        selector = str(action.get("selector") or "")
        cap = self.graph.observe_capability(page_family=FAMILY, kind="row_action", label=label, selector=selector, role=str(action.get("role") or ""), scope="entity_row", risk="mutation", run_id=run_id, evidence={"url": page.url, "safe_probe": True})
        cid = cap["capability_id"]
        before = await _page_surface(page); start = len(browser.network_tab_events)
        blocked: List[Dict[str, Any]] = []
        installed = False

        async def handler(route: Any, request: Any) -> None:
            method = str(getattr(request, "method", "GET") or "GET").upper()
            url = str(getattr(request, "url", "") or "")
            path_n = _norm(urlsplit(url).path)
            dangerous_get = method == "GET" and any(token in path_n for token in MUTATION_TOKENS)
            if method in MUTATING_METHODS or dangerous_get:
                try: headers = await request.all_headers()
                except Exception: headers = {}
                body: Any = None
                try:
                    getter = getattr(request, "post_data_json", None)
                    body = getter() if callable(getter) else None
                except Exception: body = None
                if body is None:
                    body = getattr(request, "post_data", None)
                    if isinstance(body, str):
                        try: body = json.loads(body)
                        except Exception: pass
                blocked.append(mask_sensitive_data({"method": method, "url": url, "headers": headers, "payload": body, "payload_shape": _shape(body), "blocked_before_backend": True}))
                await route.abort("blockedbyclient"); return
            await route.continue_()

        click_error = ""
        try:
            await page.route("**/*", handler); installed = True
            # BrowserSession still applies its normal mutation click guard. Authorize
            # only this label after the route barrier is active, then clear immediately.
            browser.set_portal_mutation_authorization(enabled=True, allowed_labels=[label], task_id=f"safe-discovery-probe-{_norm(label)}")
            loc = page.locator(selector).first if selector else page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first
            try:
                await browser.click_and_wait(action=label, locator=loc, selector=selector)
            except Exception as exc:
                click_error = mask_sensitive_string(str(exc))
            await asyncio.sleep(0.7)
            after = await _page_surface(page)
        finally:
            browser.clear_portal_mutation_authorization()
            if installed:
                try: await page.unroute("**/*", handler)
                except Exception:
                    try: await page.unroute("**/*")
                    except Exception: pass

        net = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out_dir, start_index=start, caused_by=cid, stage=f"safe_probe_{_norm(label)}")
        contract = _form_contract(after)
        # Structural prerequisites = required controls plus confirmation/cancel actions.
        confirm_actions = [a for a in contract.get("actions", []) if _norm(a.get("label")) in {"confirm", "deploy", "migrate", "delete", "yes", "continue"}]
        cancel_actions = [a for a in contract.get("actions", []) if _norm(a.get("label")) in {"cancel", "close", "back", "no"}]
        prerequisites = {
            "required_controls": [c for c in contract.get("controls", []) if c.get("required")],
            "confirmation_actions": confirm_actions,
            "cancel_actions": cancel_actions,
            "blocked_network_requests": blocked,
            "mutation_delivery_proof": "All POST/PUT/PATCH/DELETE and mutation-token GET requests were aborted while the probe authorization was active.",
        }
        self.graph.add_relation(source=cid, relation="requires_confirmation", target=f"state-{contract['structural_fingerprint']}", evidence={"required_control_count": len(prerequisites["required_controls"]), "confirmation_count": len(confirm_actions)})
        if blocked or contract.get("dialog_count") or contract.get("control_count"):
            self.graph.mark_success(cid)
        result = {
            "pass": bool(blocked or contract.get("dialog_count") or contract.get("control_count")),
            "capability_id": cid, "label": label, "click_error": click_error,
            "blocked_before_backend": True, "blocked_requests": blocked, "network": net,
            "form_contract": contract, "prerequisites": prerequisites,
            "state_delta": _surface_delta(before, after),
        }
        result["agentq"] = await self._agentq_transition(agentq=agentq, browser=browser, label=f"safe probe {label}", before=before, after=after, success=bool(result["pass"]), capability_id=cid)
        result["closed"] = await self._close_surface(browser, after)
        safe_write_json(out_dir / f"safe_probe_{_norm(label)}.json", result)
        return mask_sensitive_data(result)

    def _build_replay_profile(self, *, run_id: str, search_cid: str, expand_cid: str, action_results: Mapping[str, Any]) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = [{"type": "navigate", "action": "open_data_maps", "wait_for": ["listing_ready"]}]
        if search_cid:
            steps.append({"type": "search", "capability_id": search_cid, "value_source": "current_task.entity", "scope": "listing_surface", "wait_for": ["search_results_settled"]})
        if expand_cid:
            steps.append({"type": "expand", "capability_id": expand_cid, "scope": "matching_entity_row", "wait_for": ["row_actions_visible"]})
        verified = bool(search_cid and expand_cid)
        profiles: List[Dict[str, Any]] = []
        for label, result in action_results.items():
            if not isinstance(result, Mapping) or not result.get("capability_id"):
                continue
            risk = classify_risk(label)
            action_steps = list(steps) + [{"type": "action", "action": _norm(label), "capability_id": result.get("capability_id"), "scope": "matching_entity_row", "wait_for": ["resulting_surface_settled"]}]
            profile = self.graph.observe_replay_profile(
                page_family=FAMILY, name=f"entity_{_norm(label)}", steps=action_steps,
                preconditions=["authenticated_shared_browser", "data_maps_listing_ready", "entity_exists"],
                verified=bool(verified and result.get("pass") and risk in {"read", "draft"}),
                evidence={"run_id": run_id, "action": _norm(label), "risk": risk, "mutation_probe": risk == "mutation"}, run_id=run_id,
            )
            profiles.append(profile)
        return {"profiles": profiles, "verified_count": sum(1 for p in profiles if p.get("verified"))}

    async def run(self, *, ctx: RunContext, input_json: str | Path) -> Dict[str, Any]:
        payload = json.loads(Path(input_json).read_text(encoding="utf-8"))
        objects = payload.get("objects") if isinstance(payload.get("objects"), Mapping) else {}
        dm = objects.get("data_map") if isinstance(objects.get("data_map"), Mapping) else {}
        query = str(dm.get("map_identifier") or dm.get("map_name") or "").strip()
        root = Path(ctx.run_dir); out = root / "deep_discovery" / FAMILY; out.mkdir(parents=True, exist_ok=True)
        agentq = HIPAgentQController(memory_root=Path(self.config.reporting.memory_dir), run_dir=root / "agentq_datamap_deep", config=self.config)
        summary: Dict[str, Any] = {
            "schema_version": "hip.datamap-deep-discovery.v1", "run_id": ctx.run_id, "started_at": utc_now(),
            "page_family": FAMILY, "query_source": "input_json.objects.data_map", "query_value_stored": False,
            "safe_discovery": True, "mutation_probe_network_abort": True,
        }
        async with BrowserSession(self.config, root) as browser:
            await agentq.start()
            try:
                browser._active_phase_name = "deep_discovery_data_maps"; browser._current_stage = "deep_discovery:data_maps:open"
                await browser.goto_base_and_complete_sso(DATAMAP_URL); await browser.wait_ready()
                open_start = len(browser.network_tab_events)
                initial = await self.discovery._learn_surface(browser=browser, family=FAMILY, run_id=ctx.run_id, output_dir=out / "initial")
                summary["initial"] = initial
                summary["open_network"] = self.discovery._persist_network_slice(browser=browser, family=FAMILY, output_dir=out, start_index=open_start, stage="deep_open")
                surface = initial["surface"]
                await agentq.begin_phase(page=browser.page, phase="data_map_discovery", controls=list(surface.get("controls", []) or []), surface_gate={"safe_discovery": True})

                # 1. Listing primitives: search, filters, pagination.
                search = _search_candidate(surface); search_cid = ""
                if search:
                    cap = self.graph.observe_capability(page_family=FAMILY, kind="search", label=str(search.get("label") or search.get("placeholder") or "Search"), selector=str(search.get("selector") or ""), role=str(search.get("role") or ""), placeholder=str(search.get("placeholder") or ""), scope="listing_surface", run_id=ctx.run_id, evidence={"url": surface.get("url")})
                    search_cid = cap["capability_id"]
                filters = _listing_filter_candidates(surface)
                summary["listing_filters"] = [await self._inspect_filter(browser=browser, control=c, run_id=ctx.run_id, out_dir=out / "filters") for c in filters[:12]]
                pagination = _pagination_candidates(surface)
                pagination_caps = []
                for p in pagination:
                    label = _action_label(p) or "Page navigation"
                    cap = self.graph.observe_capability(page_family=FAMILY, kind="pagination", label=label, selector=str(p.get("selector") or ""), role=str(p.get("role") or ""), scope="listing_surface", run_id=ctx.run_id, evidence={"url": surface.get("url")})
                    pagination_caps.append({"capability_id": cap["capability_id"], "label": label, "selector": p.get("selector")})
                summary["pagination_capabilities"] = pagination_caps
                # Safely validate one Next/Previous transition if present.
                next_action = next((p for p in pagination if "next" in _norm(_action_label(p))), None)
                if next_action:
                    before = await _page_surface(browser.page)
                    res = await self.discovery._safe_action(browser=browser, family=FAMILY, action=next_action, output_dir=out / "pagination", run_id=ctx.run_id, stage="pagination_next")
                    summary["pagination_next_validation"] = res
                    if res.get("pass"):
                        after = res.get("post_surface", {}).get("surface", {})
                        summary["pagination_next_agentq"] = await self._agentq_transition(agentq=agentq, browser=browser, label="pagination next", before=before, after=after, success=True, capability_id=str(res.get("capability_id") or ""))
                        prev = next((p for p in _pagination_candidates(after) if "previous" in _norm(_action_label(p)) or _norm(_action_label(p)) == "prev"), None)
                        if prev:
                            summary["pagination_restore"] = await self.discovery._safe_action(browser=browser, family=FAMILY, action=prev, output_dir=out / "pagination", run_id=ctx.run_id, stage="pagination_previous")

                # 2. Search target and expand exact entity row.
                restored = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "target_restore")
                surface = restored.get("surface", {})
                if restored.get("search_capability_id"): search_cid = str(restored["search_capability_id"])
                expand_cid = str(restored.get("expand_capability_id") or "")
                summary["target_row"] = {"searched": bool(query and search_cid), "expanded": bool(expand_cid), "query_value_stored": False}

                # 3. Inventory every revealed row action and relation to Expand.
                actions = _row_actions(surface, query=query)
                inventory = []
                for action in actions:
                    cap = self.graph.observe_capability(page_family=FAMILY, kind="row_action", label=str(action.get("label") or _action_label(action)), selector=str(action.get("selector") or ""), role=str(action.get("role") or ""), scope="entity_row", risk=str(action.get("risk") or classify_risk(str(action.get("label") or ""))), parent_capability_id=expand_cid, run_id=ctx.run_id, evidence={"url": surface.get("url")})
                    if expand_cid: self.graph.add_relation(source=expand_cid, relation="reveals", target=cap["capability_id"])
                    inventory.append({"capability_id": cap["capability_id"], "label": cap.get("label"), "risk": cap.get("risk")})
                summary["row_action_inventory"] = inventory

                # 4. Inspect every safe draft/read action one-by-one from a restored state.
                action_results: Dict[str, Any] = {}
                for token in DRAFT_INSPECT_ACTIONS:
                    restored = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "restore_between_actions")
                    current = restored.get("surface", {})
                    candidate = next((a for a in _row_actions(current, query=query) if token in _norm(a.get("label"))), None)
                    if not candidate: continue
                    label = str(candidate.get("label") or token)
                    if _norm(label) in {_norm(x) for x in action_results}: continue
                    action_results[label] = await self._inspect_draft_action(browser=browser, agentq=agentq, action=candidate, query=query, run_id=ctx.run_id, out_dir=out / "draft_actions" / _norm(label))

                # 5. Probe mutation-grade action prerequisites behind network abort.
                for token in MUTATION_PROBE_ACTIONS:
                    restored = await self._restore_searched_expanded(browser=browser, query=query, run_id=ctx.run_id, out_dir=out / "restore_between_mutation_probes")
                    current = restored.get("surface", {})
                    candidate = next((a for a in _row_actions(current, query=query) if token in _norm(a.get("label"))), None)
                    if not candidate: continue
                    label = str(candidate.get("label") or token)
                    action_results[label] = await self._probe_mutation_action(browser=browser, agentq=agentq, action=candidate, run_id=ctx.run_id, out_dir=out / "mutation_probes" / _norm(label))

                summary["action_results"] = action_results
                summary["deterministic_replay"] = self._build_replay_profile(run_id=ctx.run_id, search_cid=search_cid, expand_cid=expand_cid, action_results=action_results)
                summary["capability_graph_manifest"] = self.graph.manifest()
                self.graph.save()
                await agentq.finalize_phase(page=browser.page, phase="data_map_discovery", controls=list((await _page_surface(browser.page)).get("controls", []) or []), success=bool(search_cid and expand_cid), surface_gate={"safe_discovery": True})
            finally:
                await agentq.close(); await browser.flush_logs()
        summary["finished_at"] = utc_now()
        safe_write_json(root / "datamap_deep_discovery_summary.json", summary)
        self.graph.save()
        return mask_sensitive_data(summary)
