"""V243R39: BizFlow end to end as on the live portal, and every phase learns every operation.

The live BizFlow path -- "+ Add", the flow-template card, its link, the Create
Biz Flow wizard whose tabs ahead are locked and only Next moves forward, and a
last tab with a routing table, column menus, row actions, Previous and Submit
-- is now what ``hip_portal_sim`` serves.  Against it:

* the template card's link (``javascript:void(0)``) was skipped, so the agent
  went through the card's ⋮ menu instead;
* a locked tab header could not be opened (``HIP_BIZFLOW_TAB_NOT_OPENED``) and
  Next was looked up as "the first button saying Next" -- the table pager's;
* the row "+" finder took the Flow Details *tab header* for the Attributes "+"
  and the wizard jumped back a tab;
* the routing tab's options were never learned;
* Edit / Clone / Migrate / Deploy were learned only on request, never by a
  mission, and nothing showed which phase knew which operation.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hip_portal_sim import HipPortalSim, mission_input, sim_session_config
from phase_replica_support import chromium_path, replica_html

pytestmark = pytest.mark.skipif(chromium_path() is None, reason="Chromium unavailable")


async def _sim_page(pw, sim: HipPortalSim):
    browser = await pw.chromium.launch(executable_path=chromium_path(), args=sim.launch_args)
    page = await browser.new_page(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
    return browser, page


async def _wizard_page(pw, *, hooks: bool = True):
    browser = await pw.chromium.launch(executable_path=chromium_path())
    page = await browser.new_page(viewport={"width": 1400, "height": 900})
    setup = "window.__livePlus = true; window.__liveWizard = true;" + (" window.__liveWizardTestHooks = true;" if hooks else "")
    await page.set_content(replica_html("bizflow_wizard_dds.html", setup))
    await page.wait_for_timeout(200)
    return browser, page


# ------------------------------------------------------------------ the portal copy
def test_sim_bizflow_add_shows_the_template_card_and_its_link_opens_the_wizard(tmp_path: Path):
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        with HipPortalSim(tmp_path / "cert") as sim:
            async with async_playwright() as pw:
                browser, page = await _sim_page(pw, sim)
                await page.goto(sim.url("bizflows"))
                await page.wait_for_timeout(600)
                listing = await page.inner_text("main")
                await page.click("#add")
                await page.wait_for_timeout(500)
                picker = await page.evaluate("() => ({cards: Array.from(document.querySelectorAll('.template-card')).map(c => c.dataset.template),"
                                             " search: !!document.querySelector('input[placeholder=\"Search flow templates\"]'),"
                                             " menuHidden: document.querySelector('.template-card [role=menu]').hidden})")
                await page.click(".template-card[data-template='B2B-Flow-PubSub-Template'] a.template-link")
                await page.wait_for_timeout(600)
                tabs = await page.eval_on_selector_all("[role=tab]", "els => els.map(e => [e.textContent, e.getAttribute('aria-selected'), e.getAttribute('aria-disabled')])")
                bar = await page.inner_text(".wizard-actions")
                await browser.close()
                return {"listing": listing, "picker": picker, "tabs": tabs, "bar": bar}

    out = asyncio.run(run())
    assert "Manage Biz Flow" in out["listing"] and "+ Add" in out["listing"]
    assert out["picker"]["cards"][0] == "B2B-Flow-PubSub-Template" and out["picker"]["search"] and out["picker"]["menuHidden"]
    assert out["tabs"][0] == ["Flow Details", "true", None]
    assert all(t[2] == "true" for t in out["tabs"][1:])          # tabs ahead are locked
    assert "Reset" in out["bar"] and "Next" in out["bar"] and "Previous" not in out["bar"]


def test_template_link_is_followed_and_remembered(tmp_path: Path):
    from hip_id_agent.bizflow_kb import _click_bizflow_template_link_after_add, _is_bizflow_form_surface
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        with HipPortalSim(tmp_path / "cert") as sim:
            async with async_playwright() as pw:
                browser, page = await _sim_page(pw, sim)
                await page.goto(sim.url("bizflows"))
                await page.wait_for_timeout(500)
                await page.click("#add")
                await page.wait_for_timeout(500)
                audit = await _click_bizflow_template_link_after_add(page)
                form = await _is_bizflow_form_surface(page)
                chosen = await page.evaluate("() => window.__hipTemplateChosen")
                entry = getattr(page, "_hip_bizflow_template_entry", {})
                await browser.close()
                return {"audit": audit, "form": form, "chosen": chosen, "entry": entry}

    out = asyncio.run(run())
    assert out["audit"]["method"] == "direct_template_card_link", out["audit"]
    assert out["audit"]["label"] == "B2B-Flow-PubSub-Template"
    assert out["form"] and out["chosen"] == "B2B-Flow-PubSub-Template"
    assert out["entry"].get("method") == "direct_template_card_link"


# ------------------------------------------------------------------ the wizard
def test_next_refuses_until_the_tab_is_filled_and_names_what_is_missing():
    from hip_id_agent.bizflow_kb import _click_bizflow_wizard_button
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        async with async_playwright() as pw:
            browser, page = await _wizard_page(pw)
            refused = await _click_bizflow_wizard_button(page, "next")
            await page.fill("input[formcontrolname=businessFlowName]", "U-HAUL_PC_856_ANS_MAPPING_OB")
            await page.fill("textarea[formcontrolname=flowDescription]", "Outbound 856")
            moved = await _click_bizflow_wizard_button(page, "next")
            back = await _click_bizflow_wizard_button(page, "previous")
            await browser.close()
            return {"refused": refused, "moved": moved, "back": back}

    out = asyncio.run(run())
    assert out["refused"]["blocked"] and not out["refused"]["advanced"]
    assert "Business Flow Name *" in out["refused"]["missing_fields"]
    assert any("required" in a.lower() for a in out["refused"]["alerts"])
    assert out["moved"]["advanced"] and out["moved"]["to"] == "Configure Source"
    assert out["back"]["advanced"] and out["back"]["to"] == "Flow Details"


def test_a_locked_tab_is_reached_with_next_and_a_refused_next_is_reported():
    from hip_id_agent.bizflow_kb import _ensure_bizflow_tab_open
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        async with async_playwright() as pw:
            browser, page = await _wizard_page(pw)
            try:
                await _ensure_bizflow_tab_open(page, "Source Details")
                blocked = ""
            except RuntimeError as exc:
                blocked = str(exc)
            await page.fill("input[formcontrolname=businessFlowName]", "X")
            await page.fill("textarea[formcontrolname=flowDescription]", "Y")
            opened = await _ensure_bizflow_tab_open(page, "Source Details")
            again = await _ensure_bizflow_tab_open(page, "Basic Details")   # a reached tab opens from its header
            await browser.close()
            return {"blocked": blocked, "opened": opened, "again": again}

    out = asyncio.run(run())
    assert out["blocked"].startswith("HIP_BIZFLOW_NEXT_BLOCKED") and "Business Flow Name" in out["blocked"]
    assert out["opened"]["pass"] and out["opened"]["method"] == "wizard_next"
    assert out["again"]["pass"] and out["again"]["active_tab"] == "Flow Details"


def test_the_row_plus_finder_never_takes_a_tab_header_or_the_wizard_bar():
    from hip_id_agent.bizflow_kb import _click_bizflow_section_add, _current_bizflow_tab
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        async with async_playwright() as pw:
            browser, page = await _wizard_page(pw)
            await page.evaluate("() => window.__hipWizardJump(1)")
            await page.wait_for_timeout(200)
            audit = await _click_bizflow_section_add(page, ["attribute", "attributes"], tab_label="Source Details",
                                                     section="Configure Source Attribute Rows")
            tab = await _current_bizflow_tab(page)
            await browser.close()
            return {"audit": audit, "tab": tab}

    out = asyncio.run(run())
    assert out["tab"] == "Configure Source"
    for cand in out["audit"].get("candidates") or []:
        assert cand.get("label") not in {"Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing", "Next ›", "Reset"}
        assert "tab-" not in str(cand.get("selector"))


def test_the_routing_tab_options_are_learned_without_choosing_anything():
    from hip_id_agent.bizflow_kb import learn_bizflow_tab_options
    from playwright.async_api import async_playwright

    async def run() -> Dict[str, Any]:
        async with async_playwright() as pw:
            browser, page = await _wizard_page(pw)
            await page.evaluate("() => window.__hipWizardJump(3)")
            await page.wait_for_timeout(200)
            empty = await learn_bizflow_tab_options(page, "Configure Routing")
            # A saved routing rule (what an authorized Save adds) brings the row actions.
            await page.click("#routing-add")
            await page.wait_for_timeout(200)
            await page.fill("input[formcontrolname=ruleName]", "FLOWROUTE_X")
            await page.click(".routing-save")
            await page.wait_for_timeout(200)
            with_row = await learn_bizflow_tab_options(page, "Configure Routing")
            state = await page.evaluate("() => ({submitted: window.__hipSubmitted || 0, openMenus: Array.from(document.querySelectorAll('.dds__action-menu')).filter(m => !m.hidden).length})")
            await browser.close()
            return {"empty": empty, "with_row": with_row, "state": state}

    out = asyncio.run(run())
    labels = {b["label"] for b in out["empty"]["buttons"]}
    assert {"+ Add", "‹ Previous", "Submit"} <= labels or {"+ Add", "Submit"} <= labels
    assert "Submit" in out["empty"]["commit_buttons"]
    column_menus = {k: v for k, v in out["empty"]["menus"].items() if str(v.get("where")).startswith("column:")}
    assert len(column_menus) == 5 and all(v["items"] == ["Sort Ascending", "Sort Descending", "Hide Column"] for v in column_menus.values())
    assert "Rule Name column menu" in column_menus
    row_menus = [v for k, v in out["with_row"]["menus"].items() if str(v.get("where")).startswith("row:")]
    assert row_menus and row_menus[0]["items"] == ["Edit", "View", "Delete"] and row_menus[0]["commit_items"] == ["Delete"]
    assert out["state"] == {"submitted": 0, "openMenus": 0}       # nothing chosen, every menu closed again


# ------------------------------------------------------------------ knowledge and scripts
def test_navigation_knowledge_makes_the_script_say_link_next_and_options(tmp_path: Path):
    from hip_id_agent.deterministic_script import build_script, write_phase_script
    from hip_id_agent.phase_navigation import load, record, summaries

    nav = {
        "entry": ["+ Add", "template link “B2B-Flow-PubSub-Template”"], "wizard": True,
        "tabs": [{"tab": "Flow Details", "advance": "Next ›"}, {"tab": "Configure Source", "advance": "Next ›"},
                 {"tab": "Configure Target(s)", "advance": "Next ›"}, {"tab": "Configure Routing", "bar": ["Previous", "Submit"]}],
        "options": {"Configure Routing": {"buttons": [{"label": "+ Add"}, {"label": "Submit"}],
                                          "menus": {"column:Rule Name ⋮": {"items": ["Sort Ascending", "Sort Descending"]}},
                                          "commit_buttons": ["Submit"]}},
        "commit_buttons_never_clicked": ["Submit"],
    }
    record(tmp_path, "biz_flow", nav)
    assert load(tmp_path, "biz_flow")["tabs"][0]["advance"] == "Next ›"
    graph = {"nodes": [{"input_path": "$.objects.biz_flow.flow_details.business_flow_name", "section": "Flow Details", "field_key": "business_flow_name"},
                       {"input_path": "$.objects.biz_flow.configure_source.source_type", "section": "Configure Source", "field_key": "source_type"},
                       {"input_path": "$.objects.biz_flow.configure_routing.rule.name", "section": "Configure Routing", "field_key": "name"}]}
    skill = {"skills": {"s": {"status": "candidate", "operation": "create", "bindings": {
        "flow_details.business_flow_name": {"action": "fill_text"}, "configure_source.source_type": {"action": "select_single"},
        "configure_routing.rule.name": {"action": "fill_text"}}}}}
    script = build_script(phase="biz_flow", skill_data=skill, graph=graph, navigation=load(tmp_path, "biz_flow"))
    texts = [s["text"] for s in script["steps"]]
    assert texts[1] == "Click “+ Add”" and texts[2].startswith("Click the template link “B2B-Flow-PubSub-Template”")
    assert texts.index("On the “Flow Details” tab:") < next(i for i, t in enumerate(texts) if t.startswith("Click “Next ›”"))
    assert any(t.startswith("Learned what “Configure Routing” offers") and "never clicked: Submit" in t for t in texts)
    assert summaries(tmp_path)["phases"][0]["wizard"] is True
    (tmp_path / "portal_skills").mkdir()
    (tmp_path / "portal_skills" / "biz_flow.json").write_text(json.dumps(skill))
    out = write_phase_script(phase="biz_flow", phase_dir=tmp_path / "run", memory_dir=tmp_path, graph=graph)
    assert "template link" in (tmp_path / "run" / "deterministic_script.md").read_text() and out["status"] == "candidate"


def test_operation_matrix_reports_every_phase_and_operation(tmp_path: Path):
    from hip_id_agent.edit_section_learning import EditSectionMemory, SectionMemory
    from hip_id_agent.operation_learning import OPERATIONS, operation_matrix

    EditSectionMemory(tmp_path).record("rule", {"fields": [{"label": "Name *", "read_only": True, "kind": "text"}],
                                                "surface": {"tabs": [], "buttons": ["Save", "Cancel"]}},
                                       opener={"path": ["expand row", "edit"]})
    SectionMemory(tmp_path, "deploy").record("rule", {"section_kind": "menu", "menus": {"DEV": ["TEST1", "TEST2"]}},
                                             opener={"path": ["expand row", "migrate"]})
    matrix = operation_matrix(tmp_path, ["rule", "biz_flow"])
    assert matrix["operations"] == list(OPERATIONS) and matrix["total"] == 10 and matrix["known"] == 2
    rule = matrix["rows"][0]
    assert rule["edit"]["known"] and "read-only Name *" in rule["edit"]["detail"]
    assert rule["deploy"]["known"] and rule["deploy"]["detail"].startswith("through “Migrate” (no Deploy button)")
    assert matrix["rows"][1]["clone"] == {"known": False, "status": "unknown"}


def test_api_serves_the_operation_matrix(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import backend.app as app_module

    cfg = app_module._cfg("config.yaml")
    cfg.reporting.memory_dir = str(tmp_path)
    monkeypatch.setattr(app_module, "_cfg", lambda config="config.yaml": cfg)
    (tmp_path / "deterministic_scripts").mkdir()
    (tmp_path / "deterministic_scripts" / "rule__edit.md").write_text("# Deterministic script — Rule · Edit\n")
    data = TestClient(app_module.app).get("/api/operation-matrix").json()
    assert data["schema_version"] == "hip.operation-matrix.v1" and len(data["rows"]) == 7
    assert data["operation_scripts"]["rule__edit"].startswith("# Deterministic script")
    assert "navigation" in data


def test_control_center_shows_the_operation_matrix():
    root = Path(__file__).resolve().parents[1]
    for base in ("webui", "backend/webui"):
        html = (root / base / "index.html").read_text(encoding="utf-8")
        js = (root / base / "app.js").read_text(encoding="utf-8")
        assert 'id="operationMatrixPanel"' in html and 'id="operationMatrixTable"' in html
        assert "async function loadOperationMatrix" in js and "/api/operation-matrix" in js and 'learn:"🧭"' in js


def test_config_and_mission_wire_operation_learning():
    import inspect

    from hip_id_agent.config import AppConfig
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions

    cfg = AppConfig()
    assert cfg.operation_learning.enabled and cfg.operation_learning.after_mission
    assert cfg.operation_learning.actions == ["edit", "clone", "migrate", "deploy"]
    assert FullDummyFillOptions().learn_operations is None
    run_src = inspect.getsource(FullDummyFillE2EFlow.run)
    assert "_learn_operations_after_mission" in run_src
    helper = inspect.getsource(FullDummyFillE2EFlow._learn_operations_after_mission)
    assert "skipped_no_live_page" in helper and "operation_gate" in helper


def test_a_field_that_already_holds_its_value_does_not_name_later_clicks():
    from hip_id_agent import agent_chat

    class Feed:
        def __init__(self):
            self.lines = []

        def post(self, text, **kw):
            self.lines.append(text)
            return {}

    class Page:
        pass

    feed, page = Feed(), Page()
    agent_chat.activate(feed)
    try:
        node = {"node_id": "n1", "field_key": "flow_description", "expected_value": "x", "label": "Flow Description"}
        agent_chat.field_ready(page, phase="biz_flow", node=node, control={"label": "Flow Description"}, already=True)
        agent_chat.broker_action(page, action="click", label="hip portal dds combobox", success=True)
    finally:
        agent_chat.deactivate(feed)
    assert feed.lines[0].startswith("✓ Flow Description already shows")
    assert feed.lines[1] == "Opened a dropdown on the form"


# ------------------------------------------------------------------ real browser, real learner
def test_a_mission_phase_learns_edit_clone_migrate_deploy_read_only(tmp_path: Path):
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.operation_learning import learn_mission_operations

    class Chat:
        def __init__(self):
            self.lines = []

        def post(self, text, **kw):
            self.lines.append(text)

    async def run():
        with HipPortalSim(tmp_path / "cert") as sim:
            cfg = sim_session_config(tmp_path, sim)
            cfg.reporting.memory_dir = str(tmp_path / "memory")
            data = json.loads(mission_input(tmp_path).read_text())
            chat = Chat()
            async with BrowserSession(cfg, tmp_path / "run") as browser:
                report = await learn_mission_operations(cfg, browser, data, run_dir=tmp_path / "run",
                                                        phases=["biz_flow", "source_document_type"], chat=chat, run_id="T")
            return report, chat.lines, list(sim.posts)

    report, lines, posts = asyncio.run(run())
    assert posts == []                                             # read-only: nothing saved, migrated or deployed
    rows = {(r["phase"], r["action"]): r for r in report["rows"]}
    for action in ("edit", "clone", "migrate", "deploy"):
        assert rows[("biz_flow", action)]["pass"], rows[("biz_flow", action)]
    # input.json's Document Type is not on the listing yet: an existing one teaches the section.
    doc_edit = rows[("source_document_type", "edit")]
    assert doc_edit["pass"] and doc_edit["target_source"].startswith("first listing row"), doc_edit
    assert rows[("source_document_type", "migrate")]["pass"]
    matrix = {r["phase"]: r for r in report["matrix"]["rows"]}
    assert matrix["biz_flow"]["deploy"]["known"] and "TEST1" in matrix["biz_flow"]["deploy"]["detail"]
    assert (tmp_path / "memory" / "deterministic_scripts" / "biz_flow__edit.md").is_file()
    assert any(l.startswith("✓ BizFlow · Edit learned") for l in lines)
    assert any(l.startswith("🧭 Operations known:") for l in lines)


def test_wizard_memory_counts_only_values_read_on_their_own_tab():
    """The routing Target once read as the Target Transport Profile (same value, other tab):
    remembered, it made the map say 54/54 and filling stopped one field early."""
    from hip_id_agent.phase_live_reproof import _remember_wizard_tabs

    class Page:
        def __init__(self):
            self.state = {"epoch": "e1", "current": "Configure Target(s)"}

        async def evaluate(self, _js):
            return self.state

    page = Page()
    tgt = {"input_path": "configure_routing.actions.target", "field": "route_action_target", "row": None,
           "section": "Configure Routing", "expected": "SFTP_TGT", "status": "exact", "live": "SFTP_TGT"}
    name = {"input_path": "flow_details.business_flow_name", "field": "business_flow_name", "row": None,
            "section": "Flow Details", "expected": "U-HAUL", "status": "exact", "live": "U-HAUL"}
    asyncio.run(_remember_wizard_tabs(page, "biz_flow", [dict(tgt)]))          # matched on the wrong tab
    page.state = {"epoch": "e1", "current": "Flow Details"}
    asyncio.run(_remember_wizard_tabs(page, "biz_flow", [dict(name)]))         # read on its own tab
    page.state = {"epoch": "e1", "current": "Configure Routing"}
    rows = [dict(tgt, status="not_on_screen", live=""), dict(name, status="not_on_screen", live="")]
    remembered = asyncio.run(_remember_wizard_tabs(page, "biz_flow", rows))
    assert remembered == 1
    assert rows[0]["status"] == "not_on_screen"                                # still to be filled
    assert rows[1]["status"] == "exact" and rows[1]["on_other_tab"]
    page.state = {"epoch": "e2", "current": "Configure Routing"}               # a reopened form starts empty
    rows = [dict(name, status="not_on_screen", live="")]
    assert asyncio.run(_remember_wizard_tabs(page, "biz_flow", rows)) == 0


def test_a_wizard_phase_script_joins_every_tab_skill_and_row_tools_are_not_commits():
    from hip_id_agent.deterministic_script import _pick_skill
    from hip_id_agent.phase_navigation import is_commit

    data = {"skills": {
        "a": {"skill_id": "a", "operation": "create", "scope": "Flow Details", "status": "certified", "certified_at": "2026-10-03T10:00:00Z",
              "bindings": {"flow_details.business_flow_name": {"action": "fill_text"}}, "stats": {"replays": 2}},
        "b": {"skill_id": "b", "operation": "create", "scope": "Configure Routing", "status": "candidate", "learned_at": "2026-10-03T11:00:00Z",
              "bindings": {"configure_routing.rule.name": {"action": "fill_text"}}, "stats": {"replays": 0}},
    }}
    skill = _pick_skill(data)
    assert set(skill["bindings"]) == {"flow_details.business_flow_name", "configure_routing.rule.name"}
    assert skill["status"] == "candidate" and skill["scopes"] == ["Configure Routing", "Flow Details"]
    data["skills"]["b"].update(status="certified", certified_at="2026-10-03T12:00:00Z", stats={"replays": 1})
    assert _pick_skill(data)["status"] == "certified" and _pick_skill(data)["stats"]["replays"] == 1
    assert is_commit("Submit") and is_commit("Save") and is_commit("Delete") and is_commit("Create")
    assert not is_commit("Create Condition") and not is_commit("Remove") and not is_commit("+ Add")


def test_final_consolidation_accepts_a_phase_verified_with_learning_warnings(tmp_path: Path):
    """All seven phases complete and exact, Transport Profiles verified "pass_with_warnings"
    ("Many dropdown controls have no captured/enriched options"): the mission is complete."""
    from hip_id_agent.final_mission import FinalMissionConsolidator
    from hip_id_agent.final_mission_uat import PHASES
    from hip_id_agent.mission_controller import MissionController
    from hip_id_agent.safe_io import safe_write_json

    mission = MissionController(tmp_path, run_id="T", phases=PHASES, mode="bounded_self_heal")
    rows = []
    for phase in PHASES:
        d = tmp_path / phase
        d.mkdir(parents=True, exist_ok=True)
        status = "pass_with_warnings" if phase.endswith("transport_profile") else "pass"
        v = {"phase": phase, "status": status, "warnings": ["Many dropdown controls have no captured/enriched options."]}
        safe_write_json(d / "phase_verification.json", v, mask=False)
        safe_write_json(d / "phase_exact_state_lock.json", {"exact_completion_checkpoint": {"pass": True}}, mask=False)
        safe_write_json(d / "section_judge_gate.json", {"pass": True}, mask=False)
        mission.mark_phase_complete(phase, attempt=1, judge_pass=True)
        rows.append(v)
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES, mode="bounded_self_heal").evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=rows,
        witness_report={"status": "disabled", "pass": True}, transition_state={"pending": None})
    assert result["pass"] is True and result["application_complete"] is True
    failed = [dict(rows[0], status="failed")] + rows[1:]
    result = FinalMissionConsolidator(tmp_path, run_id="T", phases=PHASES, mode="bounded_self_heal").evaluate(
        mission=mission, terminal_gate={"pass": True}, phase_verifications=failed,
        witness_report={"status": "disabled", "pass": True}, transition_state={"pending": None})
    assert result["pass"] is False                                             # a failed verification still blocks
