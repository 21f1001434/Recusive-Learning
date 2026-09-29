"""V243R31: Clone, Deploy and Migrate on every phase -- performed, learned, remembered.

"I have also shared for Deploy, Migrate and Clone too, for all the phases; it
can perform that too."

Real browser against the phase listings (``phase_listing_support``), each with
the row expander, Edit, Clone and Migrate (a menu of target environments plus a
confirmation), and Deploy in the three shapes portals use:

* Transport Profile: Deploy opens a dialog with a Target Environment field;
* Business Flow: Deploy opens a menu of target environments, then a confirmation;
* Data Map: Deploy is a plain button whose confirmation names the next environment;
* Rule: no Deploy -- an environment is reached with Migrate.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.edit_section_learning import EditSectionLearner, SectionMemory, action_summaries
from hip_id_agent.portal_operations import PortalOperationRunner, run_portal_operations
from hip_id_agent.portal_skills import canonical_operation
from hip_id_agent.task_operations import plan_task_operations, task_operation_specs
from loader_portal_support import patch_navigation, real_session_config
from phase_listing_support import BIZFLOW, DATAMAP, RULE, TP_SRC, TP_TGT, PhaseListingPortal
from phase_replica_support import ROOT, chromium_path

CONFIRM = "ALLOW HIP MUTATION"
BIZFLOW_2 = "U-HAUL_PC_810_INV_OB"


def _needs_chromium() -> None:
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


def _specs(items: List[Any]) -> List[Dict[str, Any]]:
    return [x if isinstance(x, dict) else task_operation_specs(x)[0] for x in items]


async def _session(tmp: Path, name: str) -> BrowserSession:
    session = BrowserSession(real_session_config(tmp, loading_seconds=20), tmp / name)
    await session.start()
    patch_navigation(session)
    return session


async def _operate(tmp: Path, urls: Mapping[str, str], operations: List[Any], *, allow: bool = True) -> Dict[str, Any]:
    session = await _session(tmp, "ops")
    try:
        return await run_portal_operations(session.config, {"operations": _specs(operations)}, run_dir=tmp / "ops",
                                           allow_portal_mutation=allow, confirmation=CONFIRM if allow else "", browser=session,
                                           listing_urls=dict(urls))
    finally:
        await session.close()


async def _learn(tmp: Path, portal: Any, phase: str, target: str, actions: List[str], *, gate: bool = False) -> List[Dict[str, Any]]:
    session = await _session(tmp, "learn")
    try:
        learner = EditSectionLearner(session.config, session, tmp / "run", listing_urls={phase: portal.url})
        return [await learner.learn(phase, target=target, action=a, gate={"pass": gate}) for a in actions]
    finally:
        await session.close()


def _envs(portal: PhaseListingPortal) -> Dict[str, List[str]]:
    return {r["name"]: sorted(r["envs"]) for r in portal.records}


def _paths(portal: PhaseListingPortal) -> List[Any]:
    return [(p["path"].rsplit("/", 1)[-1], p["body"].get("to") or p["body"].get("name")) for p in portal.posts]


# ------------------------------------------------------------ perform
def test_transport_profile_deploy_dialog_existing_not_offered_and_deployed(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with PhaseListingPortal("transport_profiles") as portal:
        report = asyncio.run(_operate(tmp_path, {"source_transport_profile": portal.url}, [
            f"deploy transport profile {TP_SRC} to TEST1",   # TEST1 already holds 1.0
            f"deploy transport profile {TP_TGT} to PROD",    # the dialog offers TEST1, TEST2
            f"deploy transport profile {TP_TGT} to TEST2",
            f"migrate transport profile {TP_TGT} from DEV to TEST1",
        ]))
        posts, envs = _paths(portal), _envs(portal)
    existing, not_offered, deployed, migrated = report["operations"]
    assert existing["result"] == "EXISTING" and existing["open"]["target_check"]["source_version"] == "1.0"
    # before R31 the dialog was read as an empty menu: "TEST1 is not offered"; a PROD fill ran 136 s
    assert not_offered["result"] == "NEEDS_INPUT" and not_offered["needs_input"]["offered"] == ["TEST1", "TEST2"]
    assert not_offered["open"]["opened"] == "form" and not_offered["seconds"] < 30
    assert deployed["result"] == "SUCCESS" and deployed["status"] == "committed_and_verified", deployed.get("status")
    assert deployed["open"]["opened"] == "form" and deployed["commit"]["label"] == "deploy"
    assert deployed["action_section"]["title"] == "Deploy Transport Profile"
    assert migrated["result"] == "SUCCESS" and migrated["open"]["menu_items"] == ["TEST1", "TEST2"] and migrated["choice"] == "TEST1"
    assert posts == [("deploy", "TEST2"), ("migrate", "TEST1")]
    assert envs[TP_TGT] == ["DEV", "TEST1", "TEST2"]
    knowledge = SectionMemory(tmp_path / "memory", "deploy").load("source_transport_profile")
    assert knowledge["section_kind"] == "dialog" and knowledge["menus"] == {"DEV": ["TEST1", "TEST2"]}


def test_bizflow_deploy_menu_migrate_and_clone_on_its_own_page(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with PhaseListingPortal("bizflows") as portal:
        report = asyncio.run(_operate(tmp_path, {"biz_flow": portal.url}, [
            f"deploy the bizflow {BIZFLOW} to TEST2",
            f"migrate bizflow {BIZFLOW_2} from DEV to TEST1",
            f"clone the bizflow {BIZFLOW} as {BIZFLOW}_V2 and save",
        ]))
        posts, envs, clone = _paths(portal), _envs(portal), portal.find(f"{BIZFLOW}_V2")
    deployed, migrated, cloned = report["operations"]
    # before R31 the Deploy click stayed unreconciled: HIP_MUTATION_QUARANTINE_ACTIVE on the menu choice
    assert deployed["result"] == "SUCCESS" and deployed["open"]["menu_items"] == ["TEST1", "TEST2"] and deployed["choice"] == "TEST2"
    assert deployed["open"]["opener_outcome"]["classification"] == "opened_surface_no_write"
    assert migrated["result"] == "SUCCESS" and migrated["choice"] == "TEST1"
    assert cloned["result"] == "SUCCESS" and cloned["status"] == "committed_and_verified", cloned.get("status")
    assert cloned["after"]["source"] == "clone_edit_form" and cloned["after"]["all_seen"] is True
    assert cloned["after"]["kept_from_source"] >= 20 and cloned["after"]["differs_from_source"] == []
    assert posts == [("deploy", "TEST2"), ("migrate", "TEST1"), ("clone", BIZFLOW)]
    assert envs[BIZFLOW] == ["DEV", "TEST1", "TEST2"] and envs[BIZFLOW_2] == ["DEV", "TEST1"]
    assert clone["cloned_from"] == BIZFLOW and clone["envs"] == {"DEV": ["1.0"]}


def test_data_map_deploy_confirmation_names_the_target_and_clone_needs_a_new_name(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with PhaseListingPortal("datamaps") as portal:
        report = asyncio.run(_operate(tmp_path, {"data_map": portal.url}, [
            f"deploy data map {DATAMAP} to TEST2",   # the portal asks "... from DEV to TEST1?"
            f"deploy data map {DATAMAP} to TEST1",
            {"phase": "data_map", "operation": "clone", "target": DATAMAP, "values": {"map_identifier": f"{DATAMAP}_V2", "map_class": "Transform_V2"},
             "commit": True},
            {"phase": "data_map", "operation": "clone", "target": DATAMAP, "values": {"map_identifier": DATAMAP}, "commit": True},
        ]))
        posts, envs, clone = _paths(portal), _envs(portal), portal.find(f"{DATAMAP}_V2")
    wrong, deployed, cloned, same_name = report["operations"]
    assert wrong["result"] == "NEEDS_INPUT" and wrong["needs_input"]["offered"] == ["TEST1"]
    assert wrong["confirmation"]["target_environment"] == "TEST1" and "cancel" not in json.dumps(posts)
    assert deployed["result"] == "SUCCESS" and deployed["choice"] == "TEST1" and deployed["commit"]["label"] == "deploy"
    assert cloned["result"] == "SUCCESS" and cloned["commit"]["label"] == "submit"
    assert cloned["after"]["requested_values_seen"] == {"map_identifier": True, "map_class": True}
    assert clone and next(s["value"] for s in clone["steps"] if s["label"] == "Map Class *") == "Transform_V2"
    assert same_name["result"] == "NEEDS_INPUT" and same_name["needs_input"]["field"] == "name"
    assert posts == [("deploy", "TEST1"), ("clone", DATAMAP)] and envs[DATAMAP] == ["DEV", "TEST1"]
    confirm = SectionMemory(tmp_path / "memory", "deploy").load("data_map")["confirm"]
    assert confirm["asks_before_acting"] is True and "<object>" in confirm["text"] and DATAMAP not in confirm["text"]


def test_rule_deploy_goes_through_migrate_and_clone_keeps_the_source(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with PhaseListingPortal("rules") as portal:
        report = asyncio.run(_operate(tmp_path, {"rule": portal.url}, [
            f"deploy rule {RULE} to TEST1",
            f"migrate rule {RULE} from DEV to TEST1",    # already there
            f"clone rule {RULE} as {RULE}_V2 and save",
        ]))
        posts = _paths(portal)
    deployed, existing, cloned = report["operations"]
    assert deployed["result"] == "SUCCESS" and deployed["open"]["path"][-1] == "migrate" and deployed["choice"] == "TEST1"
    assert existing["result"] == "EXISTING"
    assert cloned["result"] == "SUCCESS" and cloned["after"]["kept_from_source"] >= 12 and cloned["after"]["differs_from_source"] == []
    assert posts == [("migrate", "TEST1"), ("clone", RULE)]


# ------------------------------------------------------------ learn (read-only)
def test_sections_are_learned_read_only_and_a_guarded_deploy_is_never_opened_without_the_gate(tmp_path):
    _needs_chromium()
    with PhaseListingPortal("transport_profiles") as portal:
        clone, migrate, deploy = asyncio.run(_learn(tmp_path / "a", portal, "source_transport_profile", TP_SRC, ["clone", "migrate", "deploy"]))
        gated = asyncio.run(_learn(tmp_path / "b", portal, "source_transport_profile", TP_SRC, ["deploy"], gate=True))[0]
        posts = list(portal.posts)
    assert clone["result"] == "SUCCESS" and clone["capture"]["surface"]["title"] == "Clone Transport Profile"
    assert clone["capture"]["record_verified"] is True
    # Migrate menus are read from the page for every environment of the object
    assert migrate["result"] == "SUCCESS" and migrate["menus"] == {"DEV": ["TEST1", "TEST2"], "TEST1": ["TEST2"]}
    # Deploy may act at once: not opened without the gate
    assert deploy["result"] == "BLOCKED" and deploy["blocked_environments"] == ["DEV", "TEST1"] and "guarded" in deploy["reason"]
    # with the gate: the dialog is read and cancelled, nothing is deployed
    assert gated["result"] == "SUCCESS" and gated["section_kind"] == "dialog"
    assert gated["menus"] == {"DEV": ["TEST1", "TEST2"], "TEST1": ["TEST2"]}
    assert [f["label"] for f in gated["dialog_fields"]] == ["Target Environment", "Comments"]
    assert posts == [] and gated["no_write_requests"] is True
    memory = SectionMemory(tmp_path / "a" / "memory", "migrate")
    assert memory.next_environments("source_transport_profile", "TEST1") == ["TEST2"]
    assert SectionMemory(tmp_path / "a" / "memory", "clone").known("source_transport_profile")
    assert not SectionMemory(tmp_path / "a" / "memory", "deploy").known("source_transport_profile")


def test_learned_menus_answer_an_unreachable_target_before_any_click_with_the_route(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with PhaseListingPortal("bizflows") as portal:
        urls = {"biz_flow": portal.url}
        asyncio.run(_operate(tmp_path, urls, [f"deploy the bizflow {BIZFLOW} to TEST2"]))       # learns Deploy from DEV
        asyncio.run(_learn(tmp_path, portal, "biz_flow", BIZFLOW, ["migrate"]))                # DEV, TEST1, TEST2 menus
        report = asyncio.run(_operate(tmp_path, urls, [f"deploy the bizflow {BIZFLOW_2} to PROD"]))
        posts = _paths(portal)
    op = report["operations"][0]
    assert op["result"] == "NEEDS_INPUT" and op["needs_input"]["offered"] == ["TEST1", "TEST2"]
    assert "nothing was clicked" in op["reason"] and "DEV > TEST2 > PROD" in op["reason"]
    assert posts == [("deploy", "TEST2")]
    assert SectionMemory(tmp_path / "memory", "migrate").route("biz_flow", "DEV", "PROD") == ["DEV", "TEST2", "PROD"]


# ------------------------------------------------------------ units
def test_requests_for_clone_deploy_migrate_and_learning_them():
    (clone,) = task_operation_specs(f"clone transport profile {TP_SRC} as {TP_SRC}_V2 and save")
    assert (clone["operation"], clone["target"], clone["values"], clone["commit"]) == ("clone", TP_SRC, {"profile_name": f"{TP_SRC}_V2"}, True)
    (flow,) = task_operation_specs(f"clone the bizflow {BIZFLOW} to {BIZFLOW}_V2 and save")
    assert flow["values"] == {"flow_details": {"business_flow_name": f"{BIZFLOW}_V2"}} and flow["target"] == BIZFLOW
    assert [(s["phase"], s["operation"]) for s in task_operation_specs("learn the deploy and migrate sections of the bizflow")] == [
        ("biz_flow", "learn_deploy"), ("biz_flow", "learn_migrate")]
    every = task_operation_specs(f"show me every action of the transport profile {TP_SRC}")
    assert [s["operation"] for s in every] == ["learn_edit", "learn_clone", "learn_deploy", "learn_migrate"] and every[0]["target"] == TP_SRC
    assert len(task_operation_specs("capture the clone, deploy and migrate options for all the phases")) == 21
    (deploy,) = task_operation_specs(f"deploy the bizflow {BIZFLOW} to TEST2")
    assert deploy["operation"] == "deploy" and deploy["values"] == {"target_environment": "TEST2"}
    plan = plan_task_operations("learn the migrate section of the rule")
    assert plan["mutation_required"] is False and "capture" in [s["type"] for s in plan["steps"]]
    assert [canonical_operation(x) for x in ("learn_deploy", "learn migrate", "learn clone", "deploy", "clone", "edit")] == [
        "learn_deploy", "learn_migrate", "learn_clone", "deploy", "clone", "edit"]


def test_routes_offers_and_confirmation_wording(tmp_path):
    memory = SectionMemory(tmp_path, "migrate")
    memory.record("rule", {"section_kind": "menu", "menus": {"DEV": ["TEST1", "TEST2"], "TEST1": ["TEST2"], "TEST2": ["PROD"]}})
    assert memory.route("rule", "DEV", "PROD") == ["DEV", "TEST2", "PROD"] and memory.route("rule", "PROD", "DEV") == []
    assert memory.load("rule")["values_stored"] is False and memory.file("rule") == tmp_path / "action_sections" / "migrate" / "rule.json"
    fields = [{"label": "Target Environment", "options": ["TEST1", "TEST2"]}, {"label": "Comments"}]
    assert PortalOperationRunner._not_offered({"target_environment": "PROD", "comments": "x"}, fields) == [
        {"field": "target_environment", "label": "Target Environment", "requested": "PROD", "offered": ["TEST1", "TEST2"]}]
    assert PortalOperationRunner._not_offered({"target_environment": "test2"}, fields) == []
    assert PortalOperationRunner._confirm_template("Deploy MAP_X version 1.0 from DEV to TEST1?", "MAP_X") == \
        "Deploy <object> version <version> from DEV to TEST1?"
    kept = PortalOperationRunner._kept_from_source(
        [{"input_key": "name", "value": "A"}, {"input_key": "b", "value": "1"}, {"input_key": "c", "value": "2", "read_only": True}],
        [{"input_key": "name", "value": "A_V2"}, {"input_key": "b", "value": "1"}, {"input_key": "c", "value": "9"}], {"name": "A_V2"})
    assert kept == {"kept_from_source": 1, "differs_from_source": []}
    assert set(action_summaries(tmp_path)) == {"edit", "clone", "deploy", "migrate"} and action_summaries(tmp_path)["migrate"]["known"] == 1
    # The read-back compares with what the form was filled with: a legacy SFTP-HAFT
    # Deployment Group is replaced by the portal's current one before the fill.
    runner = PortalOperationRunner.__new__(PortalOperationRunner)
    resolved = runner._effective_requested("source_transport_profile", {
        "profile_name": "TP_X", "profile_usage": "Sender", "deployment_group": "dce-default-sender", "interface_type": "SFTP HAFT"})
    assert resolved["deployment_group"] == "da-sender-sftphaft-dce-shared" and resolved["profile_name"] == "TP_X"
    assert runner._effective_requested("biz_flow", {"flow_details": {"business_flow_name": "F_V2"}}) == {"flow_details.business_flow_name": "F_V2"}


def test_cli_backend_and_control_center_wiring():
    from typer.testing import CliRunner

    from hip_id_agent.cli import app

    result = CliRunner().invoke(app, ["learn-action-sections", "--help"], env={"COLUMNS": "220", "TERMINAL_WIDTH": "220"})
    assert result.exit_code == 0 and "Deploy" in result.output and "--allow-portal-mutation" in result.output
    backend = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    assert "action_summaries(Path(cfg.reporting.memory_dir))" in backend and '"action_knowledge"' in backend
    for ui in (ROOT / "webui" / "app.js", ROOT / "backend" / "webui" / "app.js"):
        assert '["clone","deploy","migrate"]' in ui.read_text(encoding="utf-8")
