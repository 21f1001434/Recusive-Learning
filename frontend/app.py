from __future__ import annotations

import json
import os
from typing import Any, Dict

import requests
import streamlit as st

BACKEND = os.getenv("HIP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def api(method: str, path: str, **kwargs) -> Dict[str, Any]:
    url = BACKEND + path
    try:
        resp = requests.request(method, url, timeout=120, **kwargs)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            st.error(f"Backend {resp.status_code}: {data}")
        return data if isinstance(data, dict) else {"data": data}
    except Exception as exc:
        st.error(f"Backend unavailable: {exc}")
        return {"error": str(exc)}


st.set_page_config(page_title="HIP Browser Intelligence", layout="wide")
st.title("HIP Browser Intelligence Platform")
st.caption("AutoGen 0.7.5 + AgentQ + Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP")

status = api("GET", "/api/runtime/status")
col1, col2, col3, col4 = st.columns(4)
with col1:
    ag = status.get("autogen", {})
    st.metric("AutoGen 0.7.5", "Ready" if ag.get("pass") else "Not Ready")
with col2:
    cg = status.get("capability_graph", {})
    st.metric("Learned Capabilities", int(cg.get("capability_count") or 0))
with col3:
    proc = status.get("process", {})
    st.metric("Mission", "Running" if proc.get("running") else "Idle")
with col4:
    full_ready = status.get("full_deep_readiness", {})
    st.metric("Full HIP Readiness", "Ready" if full_ready.get("operational_readiness") else "Learning Needed")

learn_tab, knowledge_tab, readiness_tab, api_tab, task_tab, governance_tab, runs_tab = st.tabs([
    "Learn HIP", "Portal Knowledge", "Full HIP Readiness", "API Explorer", "Future Task Agent", "Change Governance", "Runs / Console"
])

with learn_tab:
    st.subheader("HIP Discovery Mission")
    st.write("Learns listing/search behavior, row expansion, visible actions, Add/Create entry points, form structure, DOM/ARIA state and UI→API contracts. Mutation-grade actions are learned but not executed.")
    c1, c2 = st.columns(2)
    config = c1.text_input("Config", "config.yaml")
    input_json = c2.text_input("Input JSON used for safe search examples", "./examples/uhaul_poasn_full_dummy_input.json")
    runs_dir = st.text_input("Runs directory", "./runs", key="learn_runs")
    require_mcp = st.checkbox("Require all three MCPs", value=True)
    full_a, full_b, full_c, full_d, full_e = st.columns([2, 1, 1, 1, 1])
    if full_a.button("Deep Learn ALL HIP + Certify", type="primary", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/full-deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp, "continue_on_family_failure": True})
        st.json(out)
    if full_b.button("Pause", disabled=(not bool(proc.get("running")) or bool(proc.get("paused"))), use_container_width=True):
        st.json(api("POST", "/api/discovery/pause"))
    if full_c.button("Resume", disabled=(not bool(proc.get("running")) or not bool(proc.get("paused"))), use_container_width=True):
        st.json(api("POST", "/api/discovery/resume"))
    if full_d.button("Stop", disabled=not bool(proc.get("running")), use_container_width=True):
        st.json(api("POST", "/api/discovery/stop"))
    if full_e.button("Refresh", use_container_width=True):
        st.rerun()
    st.caption("Pause leaves Chrome interactive so you can complete Dell SSO or inspect/recover the current form, then Resume continues the same Python mission.")
    st.caption("Full Deep Learn runs Data Maps → Document Types → Rules → Transport Profiles → BizFlows, merges capability/API/replay memory, and emits one readiness certification plus a gap queue.")
    a, b, c = st.columns(3)
    if a.button("Start Broad Learn HIP", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/discovery/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    if b.button("Deep Learn Data Maps", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/datamaps/deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    if c.button("Deep Learn Document Types", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/document-types/deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    d, e, f = st.columns(3)
    if d.button("Deep Learn Rules", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/rules/deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    if e.button("Deep Learn Transport Profiles", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/transport-profiles/deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    if f.button("Deep Learn BizFlows", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/bizflows/deep/start", json={"config": config, "input_json": input_json, "runs_dir": runs_dir, "require_mcp": require_mcp})
        st.json(out)
    st.caption("Data Maps deep learning covers listing/actions/API behavior. Document Types deep learning executes Source + Target unsaved Create forms through the complete parent-child/repeatable-Attribute graph. Rules deep learning reuses the production-safe Rule transaction. Transport Profiles deep learning learns Source/Target System Type, System/Partner/Application, Interface Type, environment/account/folder/document-type branches. BizFlow deep learning reuses the production multi-tab target-first runtime for Flow Details, Source/Flow Identifiers, Targets, nested Process Steps, Enricher filename rows, Routing +Add, Conditions/Actions, API causality, safe mutation prerequisites, and value-free deterministic replay.")
    st.divider()
    st.subheader("Execute One HIP Section")
    st.caption("Run only the section you need from input.json. Example: Transport Profiles only runs Source + Target Transport Profile and skips Data Map, Document Type, Rule and BizFlow.")
    section_payload = api("GET", "/api/sections")
    section_rows = [row for row in section_payload.get("sections", []) if row.get("id") != "all"]
    section_labels = {str(row.get("label")): row for row in section_rows}
    selected_section_label = st.selectbox("Section to execute", list(section_labels.keys()) or ["Transport Profiles only (Source + Target)"], key="section_execute_select")
    selected_section = section_labels.get(selected_section_label) or {"id": "transport-profile", "phases": ["source_transport_profile", "target_transport_profile"], "dependency_note": ""}
    sc1, sc2, sc3 = st.columns(3)
    section_until_complete = sc1.checkbox("Self-heal until section passes", value=False, key="section_until_complete")
    section_vision = sc2.checkbox("Vision verify section", value=True, key="section_vision")
    section_heavy = sc3.checkbox("Heavy section evidence", value=False, key="section_heavy")
    st.caption(f"Internal phases: {', '.join(selected_section.get('phases') or [])}. {selected_section.get('dependency_note') or ''}")
    if st.button("Run Selected Section Only", type="primary", disabled=bool(proc.get("running")), use_container_width=True):
        out = api("POST", "/api/section-run/start", json={
            "section": selected_section.get("id"), "config": config, "input_json": input_json, "runs_dir": runs_dir,
            "require_mcp": require_mcp, "vision_verify": section_vision, "full_kb_context": False,
            "runtime_self_heal": True, "until_complete": section_until_complete, "write_heavy_evidence": section_heavy,
        })
        st.json(out)
    ds = api("GET", "/api/discovery/status")
    st.code(ds.get("console_tail", "")[-20000:], language="text")

with knowledge_tab:
    st.subheader("Persistent HIP Capability Graph")
    pages = api("GET", "/api/capabilities/pages")
    families = [p.get("page_family") for p in pages.get("pages", []) if p.get("page_family")]
    family = st.selectbox("Page family", [""] + families)
    search = st.text_input("Search capabilities", "")
    risk = st.selectbox("Risk", ["", "read", "draft", "mutation"])
    caps = api("GET", "/api/capabilities", params={"page_family": family, "text": search, "risk": risk})
    st.caption(f"{caps.get('count', 0)} capabilities")
    rows = caps.get("capabilities", [])
    if rows:
        st.dataframe([{
            "ID": r.get("capability_id"), "Page": r.get("page_family"), "Kind": r.get("kind"),
            "Label": r.get("label"), "Risk": r.get("risk"), "Observed": r.get("observations"),
            "Successful": r.get("success_observations"), "APIs": len(r.get("api_contract_ids") or []),
        } for r in rows], use_container_width=True, hide_index=True)
        chosen = st.selectbox("Inspect capability", [r.get("capability_id") for r in rows])
        if chosen:
            st.json(api("GET", f"/api/capabilities/{chosen}"))
    else:
        st.info("Run Learn HIP to populate the capability graph.")
    replay = api("GET", "/api/replay-profiles", params={"page_family": family, "verified_only": False})
    if replay.get("replay_profiles"):
        st.markdown("#### Deterministic replay profiles")
        st.dataframe([{
            "ID": r.get("replay_profile_id"), "Page": r.get("page_family"), "Name": r.get("name"),
            "Verified": r.get("verified"), "Observations": r.get("observations"),
            "Successful": r.get("successful_observations"), "Steps": len(r.get("steps") or []),
        } for r in replay.get("replay_profiles", [])], use_container_width=True, hide_index=True)

with readiness_tab:
    st.subheader("Full HIP Capability Certification")
    ready = api("GET", "/api/full-deep/readiness", params={"config": config})
    cert = ready.get("certification", {})
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Operational Readiness", "PASS" if cert.get("operational_readiness") else "NOT READY")
    r2.metric("Input Contract", "PASS" if cert.get("configuration_input_readiness") else "CHECK")
    r3.metric("Blocker Gaps", int(cert.get("blocker_gap_count") or 0))
    r4.metric("Coverage Warnings", int(cert.get("warning_gap_count") or 0))
    families_ready = cert.get("families", {})
    if families_ready:
        st.dataframe([{
            "Family": family,
            "Operational Ready": row.get("operational_ready"),
            "Capabilities": (row.get("metrics") or {}).get("capability_count"),
            "API Contracts": (row.get("metrics") or {}).get("api_contract_count"),
            "Verified Replays": (row.get("metrics") or {}).get("verified_replay_count"),
            "Visible Action Coverage %": (row.get("metrics") or {}).get("action_coverage_pct"),
        } for family, row in families_ready.items()], use_container_width=True, hide_index=True)
    gaps = cert.get("gaps") or []
    if gaps:
        st.markdown("#### Capability Gap Queue")
        st.dataframe(gaps, use_container_width=True, hide_index=True)
    else:
        st.success("No capability gaps are currently recorded in the latest certification.")
    with st.expander("Certification JSON"):
        st.json(ready)


with api_tab:
    st.subheader("Observed HIP API Contracts")
    family = st.selectbox("API page family", [""] + [p.get("page_family") for p in api("GET", "/api/capabilities/pages").get("pages", []) if p.get("page_family")], key="api_family")
    method = st.selectbox("HTTP method", ["", "GET", "POST", "PUT", "PATCH", "DELETE"])
    apis = api("GET", "/api/apis", params={"page_family": family, "method": method})
    contracts = apis.get("api_contracts", [])
    if contracts:
        st.dataframe([{
            "Method": x.get("method"), "Endpoint": x.get("endpoint"), "Risk": x.get("risk"),
            "Statuses": ",".join(map(str, x.get("response_statuses") or [])), "Observed": x.get("observations"),
            "UI Causes": len(x.get("caused_by_capability_ids") or []),
        } for x in contracts], use_container_width=True, hide_index=True)
        selected = st.selectbox("Inspect API", [x.get("api_contract_id") for x in contracts])
        row = next((x for x in contracts if x.get("api_contract_id") == selected), None)
        if row: st.json(row)
    else:
        st.info("No API contracts captured for the current filter.")

with task_tab:
    st.subheader("Certified Future Task Agent")
    st.caption("Certified replay first → semantic rebinding → AgentQ/AutoGen/MCP adaptive recovery. Mutation actions never auto-retry after a click attempt.")
    task = st.text_area("Task", 'Search data map "DELLCoXMLASNXX08C_U-HAUL", expand it, then Edit')
    task_config = st.text_input("Task config", "config.yaml")
    task_runs = st.text_input("Task runs directory", "./runs")
    adaptive = st.checkbox("Allow guarded adaptive exploration/self-healing", value=True, help="Uses live DOM + certified capability graph + AutoGen 0.7.5 + MCP evidence if deterministic replay drifts.")
    p1, p2 = st.columns(2)
    if p1.button("Plan Certified Task"):
        plan = api("POST", "/api/certified-task/plan", json={"task": task, "config": task_config, "runs_dir": task_runs, "allow_adaptive_exploration": adaptive})
        st.session_state["future_plan"] = plan
    plan = st.session_state.get("future_plan")
    if plan:
        m1, m2, m3 = st.columns(3)
        m1.metric("Execution mode", str(plan.get("execution_mode") or "unknown"))
        m2.metric("Families", len(plan.get("families") or []))
        m3.metric("Unresolved", int(plan.get("unresolved_step_count") or 0))
        st.json(plan)
        mutation = bool(plan.get("mutation_required"))
        allow = st.checkbox("Explicitly allow planned portal mutation", value=False, disabled=not mutation)
        confirmation = st.text_input("Mutation confirmation", "", disabled=not mutation, help=f"Required phrase: {status.get('mutation_confirmation')}")
        if p2.button("Run Certified Task", type="primary", disabled=not bool(plan.get("pass"))):
            out = api("POST", "/api/certified-task/run", json={"task": task, "config": task_config, "runs_dir": task_runs, "allow_portal_mutation": allow, "confirmation": confirmation, "allow_adaptive_exploration": adaptive})
            st.json(out)
    st.divider()
    st.subheader("Verified cross-family task replays")
    task_replays = api("GET", "/api/certified-task/replays", params={"config": task_config, "verified_only": True})
    rows = task_replays.get("task_replay_profiles") or []
    if rows:
        st.dataframe([{
            "Replay": r.get("name"), "Families": ", ".join(r.get("families") or []),
            "Steps": len(r.get("steps") or []), "Verified": r.get("verified"),
            "Successes": r.get("successful_observations"),
        } for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info("No verified cross-family future-task replay has been promoted yet. The first successful certified task will create one.")

with governance_tab:
    st.subheader("Certified Change Execution & Governance")
    st.caption("Preview → role/policy → duplicate protection → existing three-key mutation gate → one-shot execution → API + MCP verification → hash-chained audit receipt.")
    gov_task = st.text_area("Governed task", 'Search data map "MAP_A", expand it, then Deploy', key="gov_task")
    g1, g2, g3 = st.columns(3)
    gov_config = g1.text_input("Governance config", "config.yaml", key="gov_config")
    gov_runs = g2.text_input("Governance runs directory", "./runs", key="gov_runs")
    gov_role = g3.selectbox("Operator role", ["viewer", "business", "support", "technical", "admin"], index=0)
    approval_id = st.text_input("Change approval / ticket ID (optional unless policy requires it)", "", key="gov_approval")
    gov_adaptive = st.checkbox("Allow safe adaptive recovery", value=True, key="gov_adaptive", help="Never auto-retries a mutation after a click attempt.")
    gp1, gp2 = st.columns(2)
    if gp1.button("Preview Governed Change", use_container_width=True):
        payload = api("POST", "/api/governed-change/preview", json={
            "task": gov_task, "config": gov_config, "runs_dir": gov_runs,
            "operator_role": gov_role, "approval_id": approval_id,
            "allow_adaptive_exploration": gov_adaptive,
        })
        st.session_state["governed_preview"] = payload
    gov_payload = st.session_state.get("governed_preview") or {}
    preview = gov_payload.get("change_preview") or {}
    if preview:
        a, b, c, d = st.columns(4)
        a.metric("Preview", "PASS" if preview.get("pass") else "BLOCKED")
        b.metric("Risk", str(preview.get("risk") or "unknown"))
        c.metric("Operations", int(preview.get("operation_count") or 0))
        d.metric("Duplicate", "Found" if (preview.get("idempotency") or {}).get("prior_success_found") else "No")
        if preview.get("blocking_reasons"):
            st.error(" | ".join(map(str, preview.get("blocking_reasons") or [])))
        if preview.get("operations"):
            st.dataframe([{
                "Family": x.get("page_family"), "Action": x.get("action"), "Risk": x.get("risk"),
                "Capability": x.get("capability_label") or x.get("capability_id"),
                "Observed API contracts": len(x.get("observed_api_contracts") or []),
            } for x in preview.get("operations") or []], use_container_width=True, hide_index=True)
        with st.expander("Preview / policy JSON"):
            st.json(gov_payload)
        mutation = bool(preview.get("mutation_required"))
        allow_mut = st.checkbox("Explicitly authorize the planned portal mutation", value=False, disabled=not mutation, key="gov_allow_mut")
        confirmation = st.text_input("Mutation confirmation", "", disabled=not mutation, key="gov_confirmation", help=f"Exact phrase: {status.get('mutation_confirmation')}")
        force_repeat = st.checkbox("Intentionally repeat an identical previously successful mutation", value=False, disabled=not bool((preview.get("idempotency") or {}).get("prior_success_found")), key="gov_force_repeat")
        if gp2.button("Run Governed Change", type="primary", use_container_width=True, disabled=not bool(preview.get("pass") or force_repeat)):
            out = api("POST", "/api/governed-change/run", json={
                "task": gov_task, "config": gov_config, "runs_dir": gov_runs,
                "operator_role": gov_role, "approval_id": approval_id,
                "allow_portal_mutation": allow_mut, "confirmation": confirmation,
                "force_repeat_mutation": force_repeat, "allow_adaptive_exploration": gov_adaptive,
            })
            st.json(out)
    st.divider()
    st.subheader("Change audit ledger")
    audit = api("GET", "/api/governed-change/audit", params={"config": gov_config, "limit": 50})
    st.metric("Audit events", int(audit.get("event_count") or 0))
    rows = audit.get("recent_events") or []
    if rows:
        st.dataframe([{
            "Time": r.get("timestamp"), "Event": r.get("event_type"), "Change": r.get("change_id"),
            "Risk": r.get("risk"), "Role": r.get("operator_role"), "Verified": r.get("verified"),
            "Hash": str(r.get("event_hash") or "")[:16],
        } for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info("No governed change audit events have been recorded yet.")


with runs_tab:
    st.subheader("Runs and backend console")
    runs = api("GET", "/api/runs")
    if runs.get("runs"):
        st.dataframe([{
            "Run": r.get("run_id"), "Full Deep": r.get("has_full_deep"),
            "BizFlow Deep": r.get("has_bizflow_deep"), "TP Deep": r.get("has_transport_profile_deep"),
            "Rules Deep": r.get("has_rules_deep"), "Document Types Deep": r.get("has_doctype_deep"),
            "Data Maps Deep": r.get("has_datamap_deep"), "Discovery": r.get("has_discovery_summary"),
            "Future Task": r.get("has_future_task"), "Certified Task": r.get("has_certified_future_task"),
            "Governed Change": r.get("has_governed_change"), "Path": r.get("path")
        } for r in runs.get("runs", [])], use_container_width=True, hide_index=True)
    state = api("GET", "/api/discovery/status")
    st.code(state.get("console_tail", "")[-30000:], language="text")
