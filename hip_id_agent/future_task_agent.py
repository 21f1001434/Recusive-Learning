from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .aia_client import AIAClient
from .browser_session import BrowserSession
from .capability_graph import HIPCapabilityGraph, classify_risk
from .config import AppConfig
from .models import RunContext, utc_now
from .portal_discovery_flow import DISCOVERY_FAMILIES, _event_dict, _shape
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string
from .semantic_affordance import canonical_intent, resolve_semantic_affordance
from .website_world_model import WebsiteWorldModelMemory
from .autonomous_transition_runtime import AutonomousPortalTransitionPlanner


FAMILY_ALIASES = {
    "data_maps": {"data map", "datamap", "data maps", "map"},
    "document_types": {"document type", "doctype", "document types"},
    "rules": {"rule", "rules"},
    "transport_profiles": {"transport profile", "transport profiles", "tp"},
    "bizflows": {"bizflow", "biz flow", "business flow", "flow"},
}
ACTION_ALIASES = {
    "edit": {"edit"},
    "clone": {"clone", "copy", "duplicate"},
    "migrate": {"migrate", "migration"},
    "deploy": {"deploy", "deployment"},
    "delete": {"delete", "remove"},
    "view": {"view", "details", "detail", "open"},
    "history": {"history", "versions", "version history"},
    "audit": {"audit", "audit trail"},
    "download": {"download"},
    "export": {"export"},
    "create": {"create", "add", "new"},
    "save": {"save", "update", "submit"},
    "validate": {"validate", "validation", "check", "verify"},
    "add_row": {"add row", "add condition", "add action", "add attribute", "add step", "add process step"},
    "next": {"next", "continue"},
    "back": {"back", "previous"},
    "expand": {"expand", "down arrow", "dropdown", "show details", "show actions"},
}
MUTATION_CONFIRMATION = "ALLOW HIP MUTATION"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _family_url(family: str) -> str:
    for row in DISCOVERY_FAMILIES:
        if row["family"] == family:
            return str(row["url"])
    return ""


def infer_family(task: str) -> str:
    text = str(task or "").lower()
    scored=[]
    for family, aliases in FAMILY_ALIASES.items():
        score=sum(1 for alias in aliases if alias in text)
        if score: scored.append((score,family))
    scored.sort(reverse=True)
    return scored[0][1] if scored else ""


def infer_actions(task: str) -> List[str]:
    text=str(task or "").lower()
    found=[]
    for order,(action, aliases) in enumerate(ACTION_ALIASES.items()):
        positions=[text.find(alias) for alias in aliases if alias and text.find(alias) >= 0]
        if positions:
            found.append((min(positions), order, action))
    found.sort()
    return list(dict.fromkeys(action for _,_,action in found))


def _world_phase_for_family(family: str) -> str:
    return {
        "data_maps": "data_map",
        "document_types": "document_type",
        "rules": "rule",
        "transport_profiles": "transport_profile",
        "bizflows": "biz_flow",
    }.get(str(family or ""), str(family or ""))


def infer_entity(task: str, family: str) -> str:
    text=str(task or "")
    for pattern in [r'"([^"]{3,})"', r"'([^']{3,})'"]:
        m=re.search(pattern,text)
        if m: return m.group(1).strip()
    # Search/find X then ... is the most reliable unquoted syntax.
    m=re.search(r"(?:search|find|locate)\s+(?:for\s+)?(.+?)(?:\s+(?:and|then|after|to)\s+|$)", text, flags=re.I)
    if m:
        candidate=m.group(1).strip(" .")
        candidate=re.sub(r"^(?:data\s+map|document\s+type|rule|transport\s+profile|biz\s*flow)\s+", "", candidate, flags=re.I)
        return candidate[:300]
    return ""


class HIPFutureTaskPlanner:
    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config=config; self.graph=graph
        brain_dir = str(getattr(config.brain, "directory", "portal_brain") or "portal_brain")
        self.world_model = WebsiteWorldModelMemory(
            Path(config.reporting.memory_dir) / brain_dir / "website_world_model", config=config.brain
        )
        self.transition_planner = AutonomousPortalTransitionPlanner(world_model=self.world_model)

    def _memory_action_score(self, family: str, action: str, label: str) -> float:
        try:
            hints = self.world_model.planner_hints(
                phase=_world_phase_for_family(family), action=canonical_intent(action), label=label, limit=8
            )
        except Exception:
            return 0.0
        return max([float(h.get("match_score") or 0.0) * float(h.get("confidence") or 0.0) for h in hints] or [0.0])

    def _deterministic_plan(self, task: str, family_hint: str = "") -> Dict[str, Any]:
        family=family_hint or infer_family(task)
        if not family:
            return {"pass":False,"reason":"Could not infer HIP page family from task","task":task,"steps":[]}
        entity=infer_entity(task,family)
        actions=infer_actions(task)
        steps=[{"type":"navigate","page_family":family,"url":_family_url(family)}]
        search_caps=self.graph.query_capabilities(page_family=family,text="search")
        if entity and search_caps:
            steps.append({"type":"search","capability_id":search_caps[0]["capability_id"],"value":entity})
        if entity and ("expand" in actions or any(a in actions for a in ["edit","clone","migrate","deploy","delete","view"])):
            expand_caps=[c for c in self.graph.query_capabilities(page_family=family) if c.get("kind") in {"expand","action"} and ("expand" in _norm(c.get("label")) or c.get("kind")=="expand")]
            if expand_caps:
                steps.append({"type":"expand","capability_id":expand_caps[0]["capability_id"],"entity":entity})
        for action in actions:
            if action=="expand": continue
            candidates=[]
            for cap in self.graph.query_capabilities(page_family=family):
                label=_norm(cap.get("label"))
                aliases={_norm(x) for x in ACTION_ALIASES.get(action,{action})}
                if any(alias in label or label in alias for alias in aliases): candidates.append(cap)
            if candidates:
                candidates.sort(key=lambda c:(
                    0 if c.get("kind")=="row_action" else 1,
                    -self._memory_action_score(family, action, str(c.get("label") or "")),
                    -int(c.get("success_observations") or 0),
                    -int(c.get("observations") or 0),
                ))
                cap=candidates[0]
                steps.append({
                    "type":"action","action":action,"capability_id":cap["capability_id"],"risk":cap.get("risk"),"entity":entity,
                    "world_model_score":self._memory_action_score(family, action, str(cap.get("label") or "")),
                    "requires_live_reproof":True,
                })
            else:
                hints = self.world_model.planner_hints(
                    phase=_world_phase_for_family(family), action=canonical_intent(action), limit=8
                )
                semantic_supported = canonical_intent(action) in {
                    "open_add_form","create","edit","save","validate","clone","migrate","deploy",
                    "add_row","next","back","expand","more_actions","delete","download","upload","close","retry"
                }
                if semantic_supported:
                    steps.append({
                        "type":"semantic_action","action":action,"risk":classify_risk(action),"entity":entity,
                        "world_model_hints":hints,"requires_live_reproof":True,"semantic_fallback":True,
                    })
                else:
                    steps.append({"type":"unresolved_action","action":action,"entity":entity})
        unresolved=[s for s in steps if s["type"]=="unresolved_action"]
        replay_match = None
        if not unresolved:
            planned_ids = [str(s.get("capability_id") or "") for s in steps if s.get("capability_id")]
            for profile in self.graph.replay_profiles(page_family=family, verified_only=True):
                profile_ids = [str(s.get("capability_id") or "") for s in profile.get("steps") or [] if isinstance(s, Mapping) and s.get("capability_id")]
                # A verified profile is usable only when its semantic capability chain
                # is a prefix/subsequence of the requested plan. Entity values remain runtime inputs.
                if profile_ids and all(cid in planned_ids for cid in profile_ids):
                    replay_match = profile
                    break
        return {
            "schema_version":"hip.future-task-plan.v1","pass":not unresolved,"task":task,"page_family":family,"entity":entity,
            "steps":steps,"unresolved":unresolved,"planner":"deterministic_capability_graph_plus_operational_world_model",
            "execution_mode":"deterministic_replay" if replay_match else "adaptive_semantic_transition_with_live_reproof",
            "replay_profile_id":str((replay_match or {}).get("replay_profile_id") or ""),
            "replay_preconditions":list((replay_match or {}).get("preconditions") or []),
            "world_model_phase":_world_phase_for_family(family),"world_model_operational":True,
            "memory_is_advisory":True,"live_reproof_required":True,
        }

    def plan(self, task: str, family_hint: str = "") -> Dict[str, Any]:
        base=self._deterministic_plan(task, family_hint=family_hint)
        # AutoGen may improve ordering/selection, but every capability ID must
        # still come from the persistent graph; it cannot invent executable tools.
        if not bool(getattr(self.config.aia,"enabled",False)):
            return mask_sensitive_data(base)
        try:
            candidates=self.graph.query_capabilities(page_family=base.get("page_family") or "")[:80]
            aia=AIAClient(self.config.aia)
            result=aia.json_decision(
                "You are the planner for a Dell HIP Portal browser agent. Return strict JSON only. Never invent capability IDs. Use only the supplied candidate capability IDs. Respect risk classifications and put search/expand before row actions.",
                json.dumps({"task":task,"deterministic_plan":base,"candidates":candidates},ensure_ascii=False)[:50000],
            )
            proposed=result.get("steps") if isinstance(result,Mapping) else None
            if isinstance(proposed,list):
                valid_ids=set(self.graph.data.get("capabilities",{}))
                validated=[]
                for step in proposed:
                    if not isinstance(step,Mapping): continue
                    cid=str(step.get("capability_id") or "")
                    if cid and cid not in valid_ids: continue
                    validated.append(dict(step))
                if validated:
                    base={**base,"steps":validated,"planner":"autogen_0.7.5_plus_capability_graph","autogen_used":True}
        except Exception as exc:
            base["autogen_used"]=False; base["autogen_error"]=mask_sensitive_string(str(exc))
        return mask_sensitive_data(base)


class HIPFutureTaskExecutor:
    def __init__(self, config: AppConfig, graph: HIPCapabilityGraph):
        self.config=config; self.graph=graph

    async def _locator_for_capability(self, page: Any, cap: Mapping[str,Any], *, entity: str="") -> tuple[Any,str]:
        # 1) learned stable selectors
        for selector in cap.get("selectors") or []:
            try:
                loc=page.locator(str(selector)).first
                if await loc.count() and await loc.is_visible(): return loc,str(selector)
            except Exception: pass
        label=str(cap.get("label") or "").strip(); roles=[str(x) for x in cap.get("roles") or [] if str(x)]
        # 2) row-scoped semantic action binding
        if entity and cap.get("kind") in {"row_action","expand","action"}:
            containers=page.locator('tr,[role="row"],.dds__table__row,.dds__card,[class*="card"],[class*="row"]')
            try:
                count=min(await containers.count(),500)
                for i in range(count):
                    row=containers.nth(i)
                    try:
                        txt=(await row.inner_text()).strip()
                    except Exception: continue
                    if entity.lower() not in txt.lower(): continue
                    if label:
                        for selector in ['button,[role="button"],[role="menuitem"]','a[href]']:
                            nested=row.locator(selector).filter(has_text=re.compile(re.escape(label),re.I)).first
                            try:
                                if await nested.count() and await nested.is_visible(): return nested,f'row({entity}) >> text={label}'
                            except Exception: pass
                    # unlabeled expand button
                    if cap.get("kind")=="expand":
                        nested=row.locator('button[aria-expanded="false"],[role="button"][aria-expanded="false"]').first
                        try:
                            if await nested.count() and await nested.is_visible(): return nested,f'row({entity}) >> [aria-expanded=false]'
                        except Exception: pass
            except Exception: pass
        # 3) role/name, placeholder and label fallbacks
        if label:
            for role in roles + (["button"] if cap.get("kind") in {"row_action","expand","action"} else []):
                try:
                    loc=page.get_by_role(role,name=re.compile(re.escape(label),re.I)).first
                    if await loc.count() and await loc.is_visible(): return loc,f'role={role} name={label}'
                except Exception: pass
            try:
                loc=page.get_by_text(re.compile(r'^\s*'+re.escape(label)+r'\s*$',re.I)).first
                if await loc.count() and await loc.is_visible(): return loc,f'text={label}'
            except Exception: pass
        for placeholder in cap.get("placeholders") or []:
            try:
                loc=page.get_by_placeholder(str(placeholder),exact=False).first
                if await loc.count() and await loc.is_visible(): return loc,f'placeholder={placeholder}'
            except Exception: pass
        # 4) generalized semantic-affordance fallback for icon-only portal options.
        # This allows learned capabilities such as Expand/Edit/Clone/Migrate/Deploy
        # to bind even when the current HIP release renders only a chevron, pencil,
        # copy, transfer or rocket icon. The executor's mutation gate still controls
        # whether state-changing actions may actually be clicked.
        kind=str(cap.get("kind") or "").lower()
        risk=str(cap.get("risk") or "").lower()
        intent = canonical_intent(label or kind)
        if intent not in {"add_row","open_add_form","expand","collapse","more_actions","edit","clone","migrate","deploy","delete","next","back","close","search","refresh","filter","settings","retry","download","upload","save","create"}:
            if kind == "expand": intent = "expand"
            elif kind in {"row_action","action"}:
                low=label.lower()
                for wanted in ("edit","clone","migrate","deploy","delete","download","expand"):
                    if wanted in low: intent=wanted; break
        try:
            resolved = await resolve_semantic_affordance(
                page, intent=intent, aliases=[label, entity, str(cap.get("scope") or "")],
                allow_mutation=(risk == "mutation")
            )
            selector=str(resolved.get("selector") or "")
            if resolved.get("resolved") and selector:
                loc=page.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    return loc,f'affordance={intent} selector={selector}'
        except Exception:
            pass
        raise RuntimeError(f"Could not semantically bind capability {cap.get('capability_id')} {label!r}")

    def _mutation_gate(self, plan: Mapping[str,Any], *, allow_portal_mutation: bool, confirmation: str) -> Dict[str,Any]:
        mutation_steps=[]
        for step in plan.get("steps") or []:
            if not isinstance(step,Mapping): continue
            cid=str(step.get("capability_id") or "")
            cap=self.graph.data.get("capabilities",{}).get(cid,{})
            if str(step.get("risk") or cap.get("risk") or "") == "mutation": mutation_steps.append(step)
        if not mutation_steps:
            return {"pass":True,"mutation_required":False,"allowed_labels":[]}
        env_ok=str(os.getenv("HIP_ALLOW_PORTAL_MUTATION","")).strip().upper()=="YES"
        phrase_ok=str(confirmation or "").strip()==MUTATION_CONFIRMATION
        pass_=bool(allow_portal_mutation and env_ok and phrase_ok)
        labels=[]
        for step in mutation_steps:
            cap=self.graph.data.get("capabilities",{}).get(str(step.get("capability_id") or ""),{})
            labels.append(str(cap.get("label") or step.get("action") or ""))
        return {"pass":pass_,"mutation_required":True,"explicit_flag":allow_portal_mutation,"environment_gate":env_ok,"confirmation_gate":phrase_ok,"allowed_labels":labels,"required_confirmation":MUTATION_CONFIRMATION}

    async def execute(self, *, task: str, plan: Mapping[str,Any], run_dir: str|Path, allow_portal_mutation: bool=False, confirmation: str="") -> Dict[str,Any]:
        run_dir=Path(run_dir); run_dir.mkdir(parents=True,exist_ok=True)
        gate=self._mutation_gate(plan,allow_portal_mutation=allow_portal_mutation,confirmation=confirmation)
        safe_write_json(run_dir/"mutation_gate.json",gate)
        if not gate.get("pass"):
            return {"pass":False,"status":"blocked_mutation_authorization","mutation_gate":gate,"plan":mask_sensitive_data(plan)}
        task_id=f"task-{uuid.uuid4().hex[:12]}"
        result={"schema_version":"hip.future-task-execution.v1","task_id":task_id,"task":task,"started_at":utc_now(),"steps":[],"mutation_gate":gate}
        async with BrowserSession(self.config,run_dir) as browser:
            if gate.get("mutation_required"):
                browser.set_portal_mutation_authorization(enabled=True,allowed_labels=gate.get("allowed_labels") or [],task_id=task_id)
            try:
                for step in plan.get("steps") or []:
                    if not isinstance(step,Mapping): continue
                    typ=str(step.get("type") or "")
                    if typ=="navigate":
                        await browser.goto_base_and_complete_sso(str(step.get("url") or "")); result["steps"].append({"type":typ,"pass":True,"url":step.get("url")}); continue
                    entity=str(step.get("entity") or plan.get("entity") or "")
                    start=len(browser.network_tab_events)
                    if typ == "semantic_action":
                        action_name = str(step.get("action") or "")
                        risk = str(step.get("risk") or classify_risk(action_name))
                        hints = step.get("world_model_hints") if isinstance(step.get("world_model_hints"), list) else []
                        aliases = [action_name, entity]
                        for hint in hints[:8]:
                            if isinstance(hint, Mapping):
                                ctrl = hint.get("control") if isinstance(hint.get("control"), Mapping) else {}
                                for value in (ctrl.get("label"), ctrl.get("section")):
                                    if value and str(value) not in aliases:
                                        aliases.append(str(value))
                        intent = canonical_intent(action_name)
                        semantic_error = ""
                        try:
                            await browser.click_semantic_affordance(
                                intent=intent, aliases=aliases, allow_mutation=(risk == "mutation"),
                                action_label=action_name, allow_compound_menu=True,
                            )
                        except Exception as exc:
                            semantic_error = mask_sensitive_string(str(exc))
                            # A generic Create goal on a listing normally starts by opening
                            # the top-right Add/Create drawer.  This fallback is still live
                            # semantic resolution, never a remembered selector.
                            if intent == "create":
                                await browser.click_semantic_affordance(
                                    intent="open_add_form", aliases=["Add", "Create", entity],
                                    allow_mutation=False, action_label="Open create form", allow_compound_menu=False,
                                )
                                intent = "open_add_form"
                            else:
                                raise
                        await asyncio.sleep(0.5)
                        events=[_event_dict(x) for x in browser.network_tab_events[start:]]
                        result["steps"].append(mask_sensitive_data({
                            "type":typ,"pass":True,"action":action_name,"intent":intent,"risk":risk,
                            "entity":entity,"requires_live_reproof":True,"memory_is_advisory":True,
                            "world_model_hint_count":len(hints),"initial_semantic_error":semantic_error,
                            "network_transactions":[{
                                "request_id":e.get("request_id"),"method":e.get("method"),"url":e.get("url"),
                                "status":e.get("status"),"stage":e.get("stage")
                            } for e in events],
                        }))
                        continue
                    cid=str(step.get("capability_id") or ""); cap=self.graph.data.get("capabilities",{}).get(cid)
                    if not isinstance(cap,Mapping):
                        result["steps"].append({"type":typ,"pass":False,"reason":"capability missing","capability_id":cid}); break
                    start=len(browser.network_tab_events)
                    semantic_clicked=False
                    try:
                        loc,selector=await self._locator_for_capability(browser.page,cap,entity=entity)
                    except RuntimeError:
                        # Layer 5 fallback: the target action may not exist in the DOM
                        # until the row's ellipsis/kebab menu is opened. Delegate the
                        # compound traversal to BrowserSession so AutoWebGLM policy,
                        # mutation governance, effect verification and action logging
                        # remain in the normal execution path.
                        label=str(cap.get("label") or step.get("action") or typ)
                        kind=str(cap.get("kind") or "").lower()
                        risk=str(cap.get("risk") or "").lower()
                        intent=canonical_intent(label or kind)
                        semantic_supported=intent in {"edit","clone","migrate","deploy","delete","download","expand","collapse","more_actions","next","back","close","filter","settings","retry","upload","open_add_form","add_row"}
                        if typ != "search" and semantic_supported:
                            await browser.click_semantic_affordance(
                                intent=intent, aliases=[label, entity, str(cap.get("scope") or "")],
                                allow_mutation=(risk == "mutation"), action_label=label,
                                allow_compound_menu=True,
                            )
                            semantic_clicked=True
                            loc=None; selector=f"semantic-compound:{intent}"
                        else:
                            raise
                    if typ=="search":
                        await browser.fill_and_log(locator=loc,value=str(step.get("value") or ""),selector=selector,action_type="search")
                        try: await browser.press_and_log(locator=loc,key="Enter",selector=selector)
                        except Exception: pass
                    elif not semantic_clicked:
                        await browser.click_and_wait(
                            action=str(cap.get("label") or step.get("action") or typ),
                            locator=loc, selector=selector,
                            mutation_risk=(str(cap.get("risk") or "").lower() == "mutation"),
                        )
                    await asyncio.sleep(0.5)
                    events=[_event_dict(x) for x in browser.network_tab_events[start:]]
                    api_rows=[]
                    for e in events:
                        api_rows.append(mask_sensitive_data({"request_id":e.get("request_id"),"method":e.get("method"),"url":e.get("url"),"status":e.get("status"),"request_payload":e.get("request_body_redacted"),"response_payload":e.get("response_body_redacted") if e.get("response_body_redacted") is not None else e.get("response_body_text_redacted"),"stage":e.get("stage"),"caused_by_capability_id":cid}))
                        self.graph.observe_api_contract(page_family=str(cap.get("page_family") or ""),method=str(e.get("method") or "GET"),url=str(e.get("url") or ""),request_shape=_shape(e.get("request_body_redacted")),response_status=e.get("status"),response_shape=_shape(e.get("response_body_redacted") if e.get("response_body_redacted") is not None else e.get("response_body_text_redacted")),stage="future_task",caused_by_capability_id=cid)
                    self.graph.mark_success(cid); self.graph.save()
                    result["steps"].append({"type":typ,"pass":True,"capability_id":cid,"label":cap.get("label"),"risk":cap.get("risk"),"network_transactions":api_rows})
            except Exception as exc:
                result["error"]=mask_sensitive_string(str(exc))
            finally:
                browser.clear_portal_mutation_authorization()
                await browser.flush_logs()
        result["finished_at"]=utc_now(); result["pass"]=bool(result.get("steps")) and not result.get("error") and all(bool(x.get("pass")) for x in result["steps"])
        safe_write_json(run_dir/"future_task_execution.json",result)
        return mask_sensitive_data(result)
