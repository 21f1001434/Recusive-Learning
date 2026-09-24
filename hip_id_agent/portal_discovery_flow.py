from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .agentq_runtime import HIPAgentQController
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .dummy_fill_e2e import PHASE_URLS
from .maximum_observability import MaximumObservabilityCollector
from .models import NetworkTabEvent, RunContext, utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


DISCOVERY_FAMILIES: tuple[dict[str, Any], ...] = (
    {"family": "data_maps", "phase": "data_map", "url": PHASE_URLS["data_map"]},
    {"family": "document_types", "phase": "source_document_type", "url": PHASE_URLS["source_document_type"]},
    {"family": "rules", "phase": "rule", "url": PHASE_URLS["rule"]},
    {"family": "transport_profiles", "phase": "source_transport_profile", "url": PHASE_URLS["source_transport_profile"]},
    {"family": "bizflows", "phase": "biz_flow", "url": PHASE_URLS["biz_flow"]},
)

ACTION_WORDS = {
    "edit", "clone", "migrate", "deploy", "delete", "view", "details", "detail",
    "open", "history", "audit", "download", "export", "copy", "show",
}
SAFE_INSPECT_ACTIONS = {"edit", "view", "details", "detail", "open", "history", "audit"}
MUTATING_ACTIONS = {"deploy", "delete", "migrate", "publish", "remove", "enable", "disable", "save", "submit", "create"}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _event_dict(event: Any) -> Dict[str, Any]:
    if hasattr(event, "__dict__"):
        return dict(event.__dict__)
    if isinstance(event, Mapping):
        return dict(event)
    return {}


def _shape(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "<depth>"
    if isinstance(value, Mapping):
        return {str(k): _shape(v, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    if value is None:
        return None
    return type(value).__name__


def _example_queries(input_payload: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    objects = (input_payload or {}).get("objects") if isinstance((input_payload or {}).get("objects"), Mapping) else {}
    def take(*paths: Sequence[str]) -> List[str]:
        out: List[str] = []
        for path in paths:
            cur: Any = objects
            for key in path:
                if not isinstance(cur, Mapping):
                    cur = None
                    break
                cur = cur.get(key)
            if isinstance(cur, str) and cur.strip() and cur.strip() not in out:
                out.append(cur.strip())
        return out
    return {
        "data_maps": take(("data_map", "map_identifier"), ("data_map", "map_name")),
        "document_types": take(("source_document_type", "name"), ("target_document_type", "name")),
        "rules": take(("rule", "name"), ("biz_flow", "configure_routing", "rule", "name")),
        "transport_profiles": take(("source_transport_profile", "profile_name"), ("target_transport_profile", "profile_name")),
        "bizflows": take(("biz_flow", "flow_details", "business_flow_name")),
    }


async def _page_surface(page: Any) -> Dict[str, Any]:
    raw = await page.evaluate(
        r"""() => {
          const txt = el => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || el?.getAttribute?.('title') || '').replace(/\s+/g,' ').trim();
          const visible = el => { if(!el || !el.isConnected) return false; const s=getComputedStyle(el),r=el.getBoundingClientRect(); return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)!==0&&r.width>0&&r.height>0; };
          const label = el => {
            const aria=el.getAttribute?.('aria-label'); if(aria) return aria.trim();
            const by=el.getAttribute?.('aria-labelledby'); if(by){ const t=by.split(/\s+/).map(id=>txt(document.getElementById(id))).filter(Boolean).join(' '); if(t) return t; }
            if(el.id){ const lab=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(lab && txt(lab)) return txt(lab); }
            return txt(el.closest?.('dds-form-field,.dds__form-group,.form-group')?.querySelector?.('label,[class*="label"]'));
          };
          const selector = el => {
            const fc=el.getAttribute?.('formcontrolname'); if(fc) return `[formcontrolname="${CSS.escape(fc)}"]`;
            const name=el.getAttribute?.('name'); if(name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
            if(el.id) return `#${CSS.escape(el.id)}`;
            const role=el.getAttribute?.('role'), aria=el.getAttribute?.('aria-label');
            if(role && aria) return `[role="${CSS.escape(role)}"][aria-label="${CSS.escape(aria)}"]`;
            return '';
          };
          const nearestScope = el => {
            const row=el.closest?.('tr,[role="row"],.dds__table__row,.dds__card,[class*="card"],[class*="row"]');
            return txt(row).slice(0,1000);
          };
          const controls=[...document.querySelectorAll('input,textarea,select,[role="searchbox"],[role="combobox"],[role="checkbox"],[role="radio"],[role="switch"]')].filter(visible).slice(0,1200).map((el,index)=>({index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',type:el.getAttribute('type')||'',label:label(el),placeholder:el.getAttribute('placeholder')||'',formControlName:el.getAttribute('formcontrolname')||'',selector:selector(el),expanded:el.getAttribute('aria-expanded')||'',owns:el.getAttribute('aria-owns')||'',controls:el.getAttribute('aria-controls')||'',scope:nearestScope(el),disabled:!!el.disabled,required:!!el.required||el.getAttribute('aria-required')==='true',readOnly:!!el.readOnly,ariaInvalid:el.getAttribute('aria-invalid')||'',checked:!!el.checked}));
          const actions=[...document.querySelectorAll('button,[role="button"],a[href],[role="menuitem"]')].filter(visible).slice(0,1800).map((el,index)=>({index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',text:txt(el).slice(0,500),ariaLabel:el.getAttribute('aria-label')||'',title:el.getAttribute('title')||'',selector:selector(el),expanded:el.getAttribute('aria-expanded')||'',scope:nearestScope(el),disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true'),href:el.getAttribute('href')||''}));
          const rows=[...document.querySelectorAll('tr,[role="row"],.dds__table__row,.dds__card,[class*="card"]')].filter(visible).slice(0,500).map((el,index)=>({index,text:txt(el).slice(0,2500),classes:String(el.className||'').slice(0,500),id:el.id||'',buttonCount:el.querySelectorAll('button,[role="button"],[role="menuitem"]').length}));
          const dialogs=[...document.querySelectorAll('[role="dialog"],dds-drawer,.dds__drawer,[class*="drawer"],form')].filter(visible).slice(0,100).map((el,index)=>({index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||'',text:txt(el).slice(0,3000),classes:String(el.className||'').slice(0,500),controlCount:el.querySelectorAll('input,textarea,select,[role="combobox"],[formcontrolname]').length}));
          return {url:location.href,title:document.title,controls,actions,rows,dialogs};
        }"""
    )
    return mask_sensitive_data(raw if isinstance(raw, Mapping) else {"raw": raw})


def _search_candidate(surface: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = []
    for c in surface.get("controls", []) or []:
        if not isinstance(c, Mapping) or c.get("disabled"):
            continue
        hay = " ".join(str(c.get(k) or "") for k in ("role", "type", "label", "placeholder", "formControlName")).lower()
        score = 0
        if c.get("role") == "searchbox" or c.get("type") == "search": score += 8
        if "search" in hay: score += 6
        if "filter" in hay: score += 4
        if c.get("tag") == "input": score += 1
        if c.get("selector"): score += 1
        if score:
            candidates.append((score, dict(c)))
    candidates.sort(key=lambda row: -row[0])
    return candidates[0][1] if candidates else None


def _action_label(action: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(action.get("text") or action.get("ariaLabel") or action.get("title") or "")).strip()


def _is_action_word(label: str) -> bool:
    norm = _norm(label)
    return any(word in norm for word in ACTION_WORDS)


def _expand_candidate(surface: Mapping[str, Any], query: str = "") -> Optional[Dict[str, Any]]:
    rows = [r for r in surface.get("rows", []) or [] if isinstance(r, Mapping)]
    query_l = str(query or "").lower()
    matched_row_texts = [str(r.get("text") or "") for r in rows if query_l and query_l in str(r.get("text") or "").lower()]
    actions = []
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping) or a.get("disabled"):
            continue
        label = _action_label(a).lower()
        scope = str(a.get("scope") or "")
        score = 0
        if str(a.get("expanded") or "").lower() == "false": score += 9
        if any(word in label for word in ("expand", "show details", "show more", "chevron", "down")): score += 7
        if not label and a.get("role") in {"button", ""}: score += 1
        if query_l and query_l in scope.lower(): score += 8
        if matched_row_texts and any(scope and scope[:100] in row for row in matched_row_texts): score += 3
        if score and a.get("selector"):
            actions.append((score, dict(a)))
    actions.sort(key=lambda row: -row[0])
    return actions[0][1] if actions else None


def _add_candidate(surface: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    candidates=[]
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping) or a.get("disabled") or not a.get("selector"):
            continue
        label=_action_label(a)
        n=_norm(label)
        score=0
        if n in {"add", "create_biz_flow", "create", "new"}: score+=8
        if n.startswith("add_") or n.startswith("new_"): score+=5
        if "create biz flow" in label.lower(): score+=7
        if score: candidates.append((score,dict(a)))
    candidates.sort(key=lambda x:-x[0])
    return candidates[0][1] if candidates else None


def _safe_close_candidates(surface: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out=[]
    for a in surface.get("actions", []) or []:
        if not isinstance(a, Mapping) or a.get("disabled") or not a.get("selector"):
            continue
        n=_norm(_action_label(a))
        if n in {"close","cancel","back"} or n.startswith("close_"):
            out.append(dict(a))
    return out


class HIPPortalDiscoveryFlow:
    """Read-only/safe exploration mission for reusable HIP portal knowledge."""

    def __init__(self, config: AppConfig, *, capability_graph: Optional[HIPCapabilityGraph] = None) -> None:
        self.config = config
        graph_root = Path(config.reporting.memory_dir) / str(config.brain.directory or "portal_brain")
        self.graph = capability_graph or HIPCapabilityGraph(graph_root)

    def _persist_network_slice(
        self,
        *,
        browser: BrowserSession,
        family: str,
        output_dir: Path,
        start_index: int,
        caused_by: str = "",
        stage: str = "",
    ) -> Dict[str, Any]:
        events = [_event_dict(x) for x in browser.network_tab_events[start_index:]]
        rows=[]
        for e in events:
            row={
                "request_id": e.get("request_id"), "method": e.get("method"), "url": e.get("url"),
                "status": e.get("status"), "resource_type": e.get("resource_type"), "stage": e.get("stage") or stage,
                "request_headers": e.get("request_headers") or {}, "request_payload": e.get("request_body_redacted"),
                "response_headers": e.get("response_headers") or {}, "response_payload": e.get("response_body_redacted") if e.get("response_body_redacted") is not None else e.get("response_body_text_redacted"),
                "response_body_capture_status": e.get("response_body_capture_status"), "mime_type": e.get("mime_type"),
                "initiator": e.get("initiator"), "caused_by_capability_id": caused_by,
            }
            rows.append(mask_sensitive_data(row))
            self.graph.observe_api_contract(
                page_family=family, method=str(row.get("method") or "GET"), url=str(row.get("url") or ""),
                request_shape=_shape(row.get("request_payload")), response_status=row.get("status"),
                response_shape=_shape(row.get("response_payload")), stage=str(row.get("stage") or stage),
                caused_by_capability_id=caused_by,
            )
        safe_write_json(output_dir / f"api_transactions_{_norm(stage or 'stage')}.json", {"transactions": rows, "count": len(rows)})
        return {"count": len(rows), "transactions": rows}

    async def _learn_surface(self, *, browser: BrowserSession, family: str, run_id: str, output_dir: Path, revealed_by: str = "") -> Dict[str, Any]:
        page = browser.page
        if page is None:
            raise RuntimeError("Browser not started")
        surface = await _page_surface(page)
        obs = MaximumObservabilityCollector(config=self.config, root_dir=output_dir, browser=browser)
        try:
            await obs.install_observers(page, phase=f"discovery_{family}", attempt=1)
        except Exception:
            pass
        manifest = await obs.capture(page=page, phase=f"discovery_{family}", stage="surface", output_dir=output_dir / "maximum_observability")
        page_row = self.graph.observe_page(
            page_family=family, url=str(surface.get("url") or ""), title=str(surface.get("title") or ""),
            fingerprint=str(manifest.get("state_fingerprint") or ""), run_id=run_id,
            evidence={"surface_file": str(output_dir / "surface.json"), "maximum_observability": manifest},
        )
        for c in surface.get("controls", []) or []:
            if not isinstance(c, Mapping): continue
            kind = "search" if "search" in " ".join(str(c.get(k) or "") for k in ("role","type","label","placeholder")).lower() else "form_control"
            cap = self.graph.observe_capability(
                page_family=family, kind=kind, label=str(c.get("label") or c.get("placeholder") or c.get("formControlName") or c.get("role") or c.get("tag") or "control"),
                selector=str(c.get("selector") or ""), role=str(c.get("role") or ""), placeholder=str(c.get("placeholder") or ""),
                scope="form_surface" if kind == "form_control" else "listing_surface", revealed_by=revealed_by, run_id=run_id,
                evidence={"url": surface.get("url"), "form_control_name": c.get("formControlName"), "expanded": c.get("expanded")},
            )
            if revealed_by:
                self.graph.add_relation(source=revealed_by, relation="reveals", target=cap["capability_id"])
        for a in surface.get("actions", []) or []:
            if not isinstance(a, Mapping): continue
            label = _action_label(a)
            if not label and not a.get("expanded"): continue
            kind = "row_action" if _is_action_word(label) else ("expand" if str(a.get("expanded") or "") in {"true","false"} else "action")
            risk = classify_risk(label)
            cap = self.graph.observe_capability(
                page_family=family, kind=kind, label=label or "unlabeled button", selector=str(a.get("selector") or ""), role=str(a.get("role") or ""),
                scope="entity_row" if str(a.get("scope") or "").strip() else "page_surface", risk=risk, revealed_by=revealed_by, run_id=run_id,
                evidence={"url": surface.get("url"), "expanded": a.get("expanded"), "href": a.get("href")},
            )
            if revealed_by:
                self.graph.add_relation(source=revealed_by, relation="reveals", target=cap["capability_id"])
        safe_write_json(output_dir / "surface.json", surface)
        return {"surface": surface, "maximum_observability": manifest, "page": page_row}

    async def _safe_action(self, *, browser: BrowserSession, family: str, action: Mapping[str, Any], output_dir: Path, run_id: str, stage: str) -> Dict[str, Any]:
        page = browser.page
        if page is None:
            return {"pass": False, "reason": "no page"}
        selector = str(action.get("selector") or "")
        label = _action_label(action) or stage
        cap = self.graph.observe_capability(page_family=family, kind="action", label=label, selector=selector, role=str(action.get("role") or ""), scope="entity_row" if str(action.get("scope") or "").strip() else "page_surface", risk=classify_risk(label), run_id=run_id, evidence={"url": page.url})
        cid = cap["capability_id"]
        if cap.get("risk") == "mutation":
            return {"pass": False, "reason": "mutation learned but not executed during discovery", "capability_id": cid, "learned_only": True}
        start = len(browser.network_tab_events)
        try:
            if selector:
                locator = page.locator(selector).first
                locator_hint = selector
            else:
                role = str(action.get("role") or "button") or "button"
                try:
                    locator = page.get_by_role(role, name=re.compile(re.escape(label), re.I)).first
                    if not await locator.count():
                        locator = page.get_by_text(re.compile(r"^\s*" + re.escape(label) + r"\s*$", re.I)).first
                except Exception:
                    locator = page.get_by_text(re.compile(r"^\s*" + re.escape(label) + r"\s*$", re.I)).first
                locator_hint = f"semantic:{role}:{label}"
            await browser.click_and_wait(action=f"discovery {label}", locator=locator, selector=selector)
            await asyncio.sleep(0.5)
            self.graph.mark_success(cid)
            post = await self._learn_surface(browser=browser, family=family, run_id=run_id, output_dir=output_dir / f"after_{_norm(stage)}", revealed_by=cid)
            net = self._persist_network_slice(browser=browser, family=family, output_dir=output_dir, start_index=start, caused_by=cid, stage=stage)
            return {"pass": True, "capability_id": cid, "post_surface": post, "network": net}
        except Exception as exc:
            return {"pass": False, "capability_id": cid, "error": mask_sensitive_string(str(exc))}

    async def run(self, *, ctx: RunContext, input_json: str | Path | None = None) -> Dict[str, Any]:
        input_payload: Dict[str, Any] = {}
        if input_json:
            try:
                input_payload = json.loads(Path(input_json).read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Could not read discovery input JSON: {exc}") from exc
        queries = _example_queries(input_payload)
        root = Path(ctx.run_dir)
        root.mkdir(parents=True, exist_ok=True)
        agentq = HIPAgentQController(memory_root=Path(self.config.reporting.memory_dir), run_dir=root / "agentq_discovery", config=self.config)
        summary: Dict[str, Any] = {"schema_version": "hip.portal-discovery-mission.v1", "run_id": ctx.run_id, "started_at": utc_now(), "families": {}, "capability_graph": str(self.graph.path), "safe_discovery": True}
        async with BrowserSession(self.config, root) as browser:
            await agentq.start()
            try:
                for index, spec in enumerate(DISCOVERY_FAMILIES, start=1):
                    family = str(spec["family"]); phase = str(spec["phase"]); url = str(spec["url"])
                    family_dir = root / "discovery" / family
                    family_dir.mkdir(parents=True, exist_ok=True)
                    browser._active_phase_name = f"discovery_{family}"
                    browser._current_stage = f"discovery:{family}:open"
                    await browser.goto_base_and_complete_sso(url)
                    await browser.wait_ready()
                    open_start = len(browser.network_tab_events)
                    initial = await self._learn_surface(browser=browser, family=family, run_id=ctx.run_id, output_dir=family_dir / "initial")
                    open_net = self._persist_network_slice(browser=browser, family=family, output_dir=family_dir, start_index=open_start, stage="form_open")
                    family_summary: Dict[str, Any] = {"url": url, "phase": phase, "initial": initial, "open_network": open_net, "queries": queries.get(family, [])}

                    # Learn search semantics using the input entity where possible.
                    query = (queries.get(family) or [""])[0]
                    surface = initial["surface"]
                    search = _search_candidate(surface)
                    if search:
                        search_cap = self.graph.observe_capability(page_family=family, kind="search", label=str(search.get("label") or search.get("placeholder") or "Search"), selector=str(search.get("selector") or ""), role=str(search.get("role") or ""), placeholder=str(search.get("placeholder") or ""), run_id=ctx.run_id, evidence={"url": surface.get("url")})
                        family_summary["search_capability_id"] = search_cap["capability_id"]
                        if query and search.get("selector"):
                            start = len(browser.network_tab_events)
                            try:
                                loc = browser.page.locator(str(search["selector"])).first
                                await browser.fill_and_log(locator=loc, value=query, selector=str(search["selector"]), action_type="search")
                                try: await browser.press_and_log(locator=loc, key="Enter", selector=str(search["selector"]))
                                except Exception: pass
                                await asyncio.sleep(0.8)
                                self.graph.mark_success(search_cap["capability_id"])
                                after_search = await self._learn_surface(browser=browser, family=family, run_id=ctx.run_id, output_dir=family_dir / "after_search", revealed_by=search_cap["capability_id"])
                                family_summary["search"] = {"pass": True, "query_redacted": "<input entity>", "surface": after_search, "network": self._persist_network_slice(browser=browser, family=family, output_dir=family_dir, start_index=start, caused_by=search_cap["capability_id"], stage="search")}
                                surface = after_search["surface"]
                            except Exception as exc:
                                family_summary["search"] = {"pass": False, "error": mask_sensitive_string(str(exc))}

                    # Expand a matching row to reveal row actions (Data Maps example).
                    expand = _expand_candidate(surface, query=query)
                    if expand:
                        family_summary["expand"] = await self._safe_action(browser=browser, family=family, action=expand, output_dir=family_dir, run_id=ctx.run_id, stage="expand_row")
                        if family_summary["expand"].get("pass"):
                            surface = family_summary["expand"]["post_surface"]["surface"]

                    # Record all revealed actions. Safe read/edit surfaces may be opened once.
                    revealed_actions=[]
                    for a in surface.get("actions", []) or []:
                        if not isinstance(a, Mapping): continue
                        label=_action_label(a)
                        if not _is_action_word(label): continue
                        cap=self.graph.observe_capability(page_family=family, kind="row_action", label=label, selector=str(a.get("selector") or ""), role=str(a.get("role") or ""), scope="entity_row", risk=classify_risk(label), run_id=ctx.run_id, evidence={"url": surface.get("url")})
                        revealed_actions.append({"capability_id": cap["capability_id"], "label": label, "risk": cap["risk"], "selector": str(a.get("selector") or "")})
                    family_summary["revealed_actions"] = revealed_actions

                    # Inspect one non-mutating row action (prefer Edit, then View/Details)
                    # so the graph learns the resulting drawer/form and APIs as well as
                    # the listing button itself. Clone/Migrate/Deploy/Delete are only
                    # inventoried during discovery because their semantics may mutate.
                    safe_inspect = None
                    for preferred in ("edit", "view", "details", "detail", "open"):
                        for a in surface.get("actions", []) or []:
                            if not isinstance(a, Mapping):
                                continue
                            label = _action_label(a)
                            if preferred in _norm(label) and classify_risk(label) in {"read", "draft"}:
                                safe_inspect = dict(a); break
                        if safe_inspect:
                            break
                    if safe_inspect:
                        inspected = await self._safe_action(
                            browser=browser, family=family, action=safe_inspect, output_dir=family_dir,
                            run_id=ctx.run_id, stage=f"inspect_{_norm(_action_label(safe_inspect))}",
                        )
                        family_summary["safe_action_inspection"] = inspected
                        if inspected.get("pass"):
                            inspect_surface = inspected.get("post_surface", {}).get("surface", {})
                            for close in _safe_close_candidates(inspect_surface):
                                try:
                                    selector = str(close.get("selector") or "")
                                    if selector:
                                        loc = browser.page.locator(selector).first
                                    else:
                                        label = _action_label(close)
                                        loc = browser.page.get_by_role(str(close.get("role") or "button") or "button", name=re.compile(re.escape(label), re.I)).first
                                    await browser.click_and_wait(action="close inspected discovery surface", locator=loc, selector=selector)
                                    await asyncio.sleep(0.4)
                                    break
                                except Exception:
                                    continue
                            try:
                                surface = (await self._learn_surface(browser=browser, family=family, run_id=ctx.run_id, output_dir=family_dir / "after_safe_inspection_close"))["surface"]
                            except Exception:
                                pass

                    # Learn the Add/Create entry point and the resulting form structure.
                    add = _add_candidate(surface)
                    if add:
                        add_result = await self._safe_action(browser=browser, family=family, action=add, output_dir=family_dir, run_id=ctx.run_id, stage="open_add_form")
                        family_summary["add_form"] = add_result
                        if add_result.get("pass"):
                            # Relate all mounted form controls to the Add action, then close/cancel without save.
                            post_surface = add_result.get("post_surface", {}).get("surface", {})
                            for close in _safe_close_candidates(post_surface):
                                try:
                                    await browser.click_and_wait(action="close discovery form", locator=browser.page.locator(str(close.get("selector"))).first, selector=str(close.get("selector")))
                                    await asyncio.sleep(0.3)
                                    break
                                except Exception:
                                    continue
                    self.graph.save()
                    safe_write_json(family_dir / "discovery_summary.json", family_summary)
                    summary["families"][family] = family_summary
            finally:
                await agentq.close()
                await browser.flush_logs()
        summary["finished_at"] = utc_now()
        summary["capability_graph_manifest"] = self.graph.manifest()
        safe_write_json(root / "portal_discovery_summary.json", summary)
        self.graph.save()
        return mask_sensitive_data(summary)
