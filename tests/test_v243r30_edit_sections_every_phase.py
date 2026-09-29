"""V243R30: the agent knows every phase's Edit section and can edit through it.

"Open the Transport Profile link, click the expand button, click Edit and
capture all the values; it can edit; the same for the BizFlow and every phase;
save it so that it already has knowledge of the Edit section."

Real browser against listings with the row expander and Edit
(``phase_listing_support``): the Transport Profile and Data Map Edit forms open
as a drawer over the listing, the Business Flow and Rule Edit forms on their
own page (BizFlow: four wizard tabs, a collapsed process step, unlabelled
rows).  The Document Type listing is the R24 replica.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.edit_section_learning import (
    ALL_PHASES, EditSectionLearner, EditSectionMemory, build_fields, compare_requested, input_json_from_fields, learn_edit_sections,
    resolve_phases,
)
from hip_id_agent.portal_operations import run_portal_operations
from hip_id_agent.portal_skills import PortalSkillStore, canonical_operation
from hip_id_agent.section_judge import _values_equal
from hip_id_agent.task_operations import plan_task_operations, task_operation_specs
from doctypes_listing_support import PHASE as DOCTYPE, DocTypesPortal
from loader_portal_support import patch_navigation, real_session_config
from phase_listing_support import BIZFLOW, DATAMAP, RULE, TP_SRC, TP_TGT, PhaseListingPortal
from phase_replica_support import ROOT, chromium_path

CONFIRM = "ALLOW HIP MUTATION"
USER_REQUEST = (
    "Now i need to main section like It need to open Transport proflie link and then click the expand button and the clcik on "
    "the Edit button and capture all the values and it can edit same need to be done for the Bizflow and all the pashe and save "
    "it so that it it already have knowledge of the edit secction too"
)


def _needs_chromium() -> None:
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


def _example(phase: str) -> Dict[str, Any]:
    return json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))["objects"][phase]


async def _session(tmp: Path, name: str) -> BrowserSession:
    cfg = real_session_config(tmp, loading_seconds=20)
    session = BrowserSession(cfg, tmp / name)
    await session.start()
    patch_navigation(session)
    return session


async def _learn(tmp: Path, portal: Any, phase: str, target: str = "") -> Dict[str, Any]:
    session = await _session(tmp, "learn")
    try:
        result = await EditSectionLearner(session.config, session, tmp / "run", listing_urls={phase: portal.url}).learn(phase, target=target)
        result["_final_url"] = session.page.url
        return result
    finally:
        await session.close()


async def _operate(tmp: Path, urls: Mapping[str, str], operations: List[Dict[str, Any]], *, allow: bool = True) -> Dict[str, Any]:
    session = await _session(tmp, "ops")
    try:
        return await run_portal_operations(session.config, {"operations": operations}, run_dir=tmp / "ops", allow_portal_mutation=allow,
                                           confirmation=CONFIRM if allow else "", browser=session, listing_urls=dict(urls))
    finally:
        await session.close()


def _field(result: Mapping[str, Any], key: str) -> Dict[str, Any]:
    return next(f for f in result["fields"] if f.get("input_key") == key)


# ------------------------------------------------------------ learn: every phase
def test_transport_profile_edit_is_opened_from_its_listing_read_completely_and_closed_unsaved(tmp_path):
    _needs_chromium()
    with PhaseListingPortal("transport_profiles") as portal:
        result = asyncio.run(_learn(tmp_path, portal, "source_transport_profile", TP_SRC))
        posts = list(portal.posts)
    assert result["result"] == "SUCCESS" and result["status"] == "edit_section_learned", result.get("status")
    # listing -> search -> the exact row (not ..._OLD) -> expander -> Edit
    assert result["open"]["path"] == ["expand row", "edit"] and result["open"]["panel"]["verified_by"] == "name_field"
    capture = result["capture"]
    assert capture["surface"]["title"] == "Edit Transport Profile" and capture["surface"]["kind"] == "drawer"
    assert capture["record_verified"] is True and capture["mapping"]["bound"] == 15
    # every input.json value of the profile is read from the Edit form, exactly
    expected = _example("source_transport_profile")
    edit_input = result["input_json"]["objects"]["source_transport_profile"]
    assert set(edit_input) == set(expected)
    assert all(_values_equal(expected[k], edit_input[k]) for k in expected), {k: (expected[k], edit_input[k]) for k in expected}
    # ... and what input.json does not know: tags, checkboxes, the portal's audit fields
    labels = {f["label"]: f for f in result["fields"]}
    assert labels["Key"]["value"] == "BU" and labels["Last Modified By"]["read_only"] and labels["Splitter Required"]["value"] is False
    assert _field(result, "profile_name")["read_only"] is True and _field(result, "existing_account")["kind"] == "radio"
    assert _field(result, "existing_account")["options"] == ["Yes", "No"]
    assert "haftatap10251108" in _field(result, "existing_account_name")["options"]
    # a password is never read out
    assert labels["Account Password"]["sensitive"] and labels["Account Password"]["value"] == "********"
    assert "S3cr3t" not in json.dumps(result) and "S3cr3t" not in Path(result["values_file"]).read_text(encoding="utf-8")
    # nothing typed, chosen or saved; the drawer is gone and the listing is back
    assert result["close"] == {"closed": True, "via": "cancel"} and result["no_write_requests"] is True and posts == []
    # the values are in the run folder, as input.json
    assert json.loads(Path(result["edit_input_file"]).read_text(encoding="utf-8"))["objects"]["source_transport_profile"]["profile_name"] == TP_SRC


def test_the_learned_edit_section_is_remembered_value_free_and_the_path_is_verified(tmp_path):
    _needs_chromium()
    with PhaseListingPortal("transport_profiles") as portal:
        first = asyncio.run(_learn(tmp_path / "a", portal, "source_transport_profile", TP_SRC))
        second = asyncio.run(_learn(tmp_path / "a", portal, "target_transport_profile", TP_TGT))
    memory = EditSectionMemory(tmp_path / "a" / "memory")
    knowledge = memory.load("source_transport_profile")
    assert knowledge["surface"]["title"] == "Edit Transport Profile" and knowledge["surface"]["commit_labels"] == ["Update"]
    assert knowledge["surface"]["close_labels"] and "Cancel" in knowledge["surface"]["close_labels"]
    assert knowledge["opener"]["path"] == ["expand row", "edit"]
    assert "Profile Name" in knowledge["read_only_fields"] and "existing_account_name" in knowledge["input_keys"]
    assert {"Account Password", "Last Modified By", "Key"} <= set(knowledge["portal_only_fields"])
    assert knowledge["values_stored"] is False and knowledge["verified"] == 1
    text = memory.file("source_transport_profile").read_text(encoding="utf-8")
    for value in ("/SFTP_U-HAUL_ASN_PC_SRC_IB", "svc_hip_portal", "S3cr3t", ".*\\\\*.", "\"value\""):
        assert value not in text, value
    # the Edit path is the one Edit operations replay: verified twice -> deterministic
    store = PortalSkillStore(tmp_path / "a" / "memory")
    assert store.opener_plan("source_transport_profile", "edit")["path"] == ["expand row", "edit"]
    assert first["learned_mode"] == "exploration" and store.load("source_transport_profile")["openers"]["edit"]["verified_outcomes"] == 1
    assert second["capture"]["record_verified"] and memory.summary("target_transport_profile")["known"]


def test_bizflow_edit_page_every_tab_collapsed_step_and_row_is_read(tmp_path):
    _needs_chromium()
    with PhaseListingPortal("bizflows") as portal:
        result = asyncio.run(_learn(tmp_path, portal, "biz_flow", BIZFLOW))
        posts, listing = list(portal.posts), portal.url
    assert result["result"] == "SUCCESS", result.get("status")
    capture = result["capture"]
    assert capture["surface"]["kind"] == "page" and capture["surface"]["title"] == "Edit Biz Flow"
    assert [t["tab"] for t in capture["tabs_read"]] == ["Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing"]
    assert capture["sections_opened"] == ["2 ::: Step"]  # the collapsed second process step was opened to read it
    obj = result["input_json"]["objects"]["biz_flow"]
    assert obj["flow_details"]["business_flow_name"] == BIZFLOW
    assert [c["value"] for c in obj["flow_identifiers"]["conditions"]] == ["uhaul", "DELL"]  # the second row has no labels
    assert [s["step_type"] for s in obj["process_steps"]] == ["Mapping Transformer", "Enricher"]
    assert obj["configure_targets"]["target_transport_profile"] == TP_TGT
    assert _field(result, "flow_details.business_flow_name")["read_only"] is True
    assert not any(f["label"] == "Table search" for f in result["fields"])  # the routing table's search box is not a field
    # Cancel returned to the listing; nothing saved
    assert result["close"]["closed"] and result["_final_url"].split("?")[0] == listing and posts == []


def test_data_map_rule_and_document_type_edit_sections(tmp_path):
    _needs_chromium()
    with PhaseListingPortal("datamaps") as maps:
        data_map = asyncio.run(_learn(tmp_path / "m", maps, "data_map", DATAMAP))
    with PhaseListingPortal("rules") as rules:
        rule = asyncio.run(_learn(tmp_path / "r", rules, "rule", RULE))
    with DocTypesPortal(live_path=True) as doctypes:
        doctype = asyncio.run(_learn(tmp_path / "d", doctypes, DOCTYPE))  # no name: the listing's first row
        posts = list(doctypes.posts)
    assert data_map["result"] == rule["result"] == doctype["result"] == "SUCCESS"
    assert _field(data_map, "map_data_file")["value"] == "Transform_DELLCoXMLASNXX08C.jar"  # shown by name next to Browse Files
    assert _field(data_map, "map_identifier_version")["value"] == "1" and _field(data_map, "status")["value"] is True
    # the Rule's Name is read-only in Edit and still its Name (not "Document Type Name (Version)")
    assert _field(rule, "name")["value"] == RULE and _field(rule, "name")["read_only"] is True
    assert _field(rule, "document_type_name_version")["value"] == "XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)"
    assert rule["input_json"]["objects"]["rule"]["conditions"]["rows"][1]["attribute_name_unit"] == "Sender"
    assert doctype["target"] == "a-doc-type-test" and doctype["target_source"] == "first_listing_row"
    assert doctype["capture"]["record_verified"] and _field(doctype, "version")["value"] == "3.0"
    assert _field(doctype, "attributes_to_configure[1].attribute_name")["value"] == "Sender" and posts == []


def test_the_request_learns_every_phase_in_the_order_asked_in_one_browser(tmp_path, monkeypatch):
    _needs_chromium()
    specs = task_operation_specs(USER_REQUEST)
    assert [s["phase"] for s in specs][:3] == ["source_transport_profile", "target_transport_profile", "biz_flow"]
    assert {s["phase"] for s in specs} == set(ALL_PHASES) and {s["operation"] for s in specs} == {"learn_edit"}
    assert not any(s["commit"] for s in specs)
    with PhaseListingPortal("transport_profiles") as tps, PhaseListingPortal("bizflows") as flows:
        urls = {"source_transport_profile": tps.url, "target_transport_profile": tps.url, "biz_flow": flows.url}
        report = asyncio.run(_operate(tmp_path, urls, [s for s in specs if s["phase"] in urls], allow=False))
        posts = tps.posts + flows.posts
    assert [(r["phase"], r["result"]) for r in report["results"]] == [
        ("source_transport_profile", "SUCCESS"), ("target_transport_profile", "SUCCESS"), ("biz_flow", "SUCCESS")]
    # no name in the request: input.json has none either, so the first row of each listing
    assert [op["target"] for op in report["operations"]] == [TP_SRC, TP_SRC, BIZFLOW]
    assert posts == []
    memory = EditSectionMemory(tmp_path / "memory")
    assert memory.summaries()["known"] == 3


# ------------------------------------------------------------ edit through the knowledge
def test_transport_profile_edit_changes_only_the_requested_values_and_reads_them_back_from_the_edit_form(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    edit = {"phase": "source_transport_profile", "operation": "edit", "target": TP_SRC, "commit": True,
            "values": {"post_transfer_action": "Delete", "file_filtering_pattern": "*.xml"}}
    rename = {"phase": "source_transport_profile", "operation": "edit", "target": TP_TGT, "commit": True, "values": {"profile_name": "SFTP_RENAMED"}}
    with PhaseListingPortal("transport_profiles") as portal:
        report = asyncio.run(_operate(tmp_path, {"source_transport_profile": portal.url}, [edit, rename]))
        posts = list(portal.posts)
        saved = {label: portal.value(TP_SRC, label) for label in ("Post Transfer Action *", "File Filtering Pattern *", "Existing Account Name *")}
    changed, renamed = report["operations"]
    assert changed["result"] == "SUCCESS" and changed["status"] == "committed_and_verified", changed.get("status")
    assert changed["edit_section"]["captured"] and changed["edit_section"]["commit_labels"] == ["Update"]
    assert changed["commit"]["label"] == "update"  # the learned Save of this Edit form
    assert [(c["field"], c["change"]) for c in changed["changes"]] == [("file_filtering_pattern", True), ("post_transfer_action", True)]
    assert changed["unrelated_changes"] == []
    assert changed["after"]["source"] == "edit_form_reopened" and changed["after"]["all_seen"] is True
    assert saved == {"Post Transfer Action *": "Delete", "File Filtering Pattern *": "*.xml", "Existing Account Name *": "haftatap10251108"}
    # a field the portal keeps read-only in Edit: stopped before anything was touched
    assert renamed["result"] == "NEEDS_INPUT" and renamed["needs_input"]["field"] == "Profile Name"
    assert "read-only" in renamed["reason"] and renamed["edit_section"]["known_before"] is True
    assert len(posts) == 1


def test_bizflow_edit_is_proved_by_a_replay_on_the_reopened_edit_page_and_saved_once(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    edit = {"phase": "biz_flow", "operation": "edit", "target": BIZFLOW, "commit": True,
            "values": {"flow_details": {"business_flow_name": BIZFLOW, "flow_description": "Outbound 856 ASN - edited by agent"}}}
    with PhaseListingPortal("bizflows") as portal:
        report = asyncio.run(_operate(tmp_path, {"biz_flow": portal.url}, [edit]))
        posts = list(portal.posts)
        saved = portal.value(BIZFLOW, "Flow Description *")
    op = report["operations"][0]
    assert op["result"] == "SUCCESS" and op["status"] == "committed_and_verified", op.get("status")
    # before R30 the new tab skills waited for the next run: commit_waiting_for_certified_skill
    assert op["fill"]["skill_status"] == "certified" and op["fill"]["reopened"] == 1
    assert op["fill"]["execution_mode"] == "learned_then_certified_by_replay"
    assert op["edit_section"]["tabs"] == ["Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing"]
    assert op["after"]["source"] == "edit_form_reopened" and op["after"]["all_seen"] is True
    assert saved == "Outbound 856 ASN - edited by agent" and len(posts) == 1


def test_a_save_the_portal_did_not_keep_is_caught_by_reading_the_edit_form_again(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    edit = {"phase": "source_transport_profile", "operation": "edit", "target": TP_SRC, "commit": True,
            "values": {"file_filtering_pattern": "*.edi"}}
    with PhaseListingPortal("transport_profiles", ignore_on_save=("File Filtering Pattern *",)) as portal:
        report = asyncio.run(_operate(tmp_path, {"source_transport_profile": portal.url}, [edit]))
    op = report["operations"][0]
    assert op["commit"]["pass"] is True and op["verification"]["pass"] is True  # "saved" and listed ...
    assert op["status"] == "committed_values_not_seen" and op["result"] == "FAILED"  # ... but not kept
    assert op["after"]["requested_values_seen"] == {"file_filtering_pattern": False}


def test_edit_operations_without_mutation_authorization_read_but_never_save(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    edit = {"phase": "rule", "operation": "edit", "target": RULE, "commit": True, "values": {"description": "changed"}}
    with PhaseListingPortal("rules") as portal:
        report = asyncio.run(_operate(tmp_path, {"rule": portal.url}, [edit], allow=False))
        posts = list(portal.posts)
    op = report["operations"][0]
    assert op["result"] == "BLOCKED" and op["status"] == "commit_not_authorized" and posts == []
    assert op["edit_section"]["captured"] and "Name" in op["edit_section"]["read_only"]


# ------------------------------------------------------------ units
def test_the_request_is_understood_as_learning_not_as_an_edit():
    (spec,) = task_operation_specs("capture the edit values of transport profile SFTP_U-HAUL_ASN_PC_SRC_IB")
    assert spec == {"phase": "source_transport_profile", "operation": "learn_edit", "source": "task_box", "commit": False, "target": TP_SRC}
    assert [s["phase"] for s in task_operation_specs("read the edit form of the transport profile and the bizflow")] == [
        "source_transport_profile", "biz_flow"]
    # "set ... to Delete" is a value, not a Delete
    ops = task_operation_specs("edit transport profile SFTP_U-HAUL_ASN_PC_SRC_IB and set post transfer action to Delete and save")
    assert [(s["operation"], s["commit"]) for s in ops] == [("edit", True)]
    assert [s["operation"] for s in task_operation_specs("delete the rule OLD_RULE")] == ["delete"]
    plan = plan_task_operations("learn the edit section of the bizflow")
    assert plan["mutation_required"] is False and [s["type"] for s in plan["steps"]] == [
        "navigate", "search", "open_row_action", "capture", "close", "remember"]
    assert canonical_operation("learn edit") == "learn_edit" and canonical_operation("edit") == "edit" and canonical_operation("update") == "edit"
    assert resolve_phases("transport_profile,bizflow") == ["source_transport_profile", "target_transport_profile", "biz_flow"]


def test_fields_group_radios_mask_secrets_and_rebuild_input_json_rows():
    controls = [
        {"id": "a", "type": "radio", "label": "Yes", "group_label": "Existing Account *", "group_name": "ea", "checked": True, "section": "S"},
        {"id": "b", "type": "radio", "label": "No", "group_label": "Existing Account *", "group_name": "ea", "checked": False, "section": "S"},
        {"id": "c", "type": "password", "label": "SFTP Password", "value": "hunter2", "section": "S"},
        {"id": "d", "role": "switch", "type": "checkbox", "label": "Status", "checked": True, "section": "S"},
        {"id": "e", "type": "text", "label": "Version", "placeholder": "1", "value": "", "disabled": True, "section": "S"},
    ]
    fields = build_fields(controls)
    assert [(f["label"], f["kind"], f["value"]) for f in fields] == [
        ("Existing Account", "radio", "Yes"), ("SFTP Password", "password", "********"), ("Status", "switch", True), ("Version", "text", "1")]
    assert fields[0]["options"] == ["Yes", "No"] and fields[0]["required"] and fields[3]["read_only"]
    fields[0]["input_key"], fields[1]["input_key"], fields[2]["input_key"] = "existing_account", "sftp_password", "status"
    rows = [dict(label="Value", value="uhaul", input_key="conditions.rows[0].value"), dict(label="Value", value="DELL", input_key="conditions.rows[1].value")]
    obj = input_json_from_fields("rule", [*fields, *rows])["objects"]["rule"]
    assert obj == {"existing_account": "Yes", "status": "Enable", "conditions": {"rows": [{"value": "uhaul"}, {"value": "DELL"}]}}
    seen = compare_requested([*fields, *rows], {"status": "Enabled", "conditions": {"rows": [{"value": "uhaul"}, {"value": "dell2"}]}})
    assert seen["requested_values_seen"] == {"status": True, "conditions.rows[0].value": True, "conditions.rows[1].value": False}


def test_a_hip_page_whose_url_holds_mapping_is_not_a_sign_in_page(tmp_path):
    from hip_id_agent.config import AppConfig

    session = BrowserSession(AppConfig(), tmp_path / "s")
    hip = "https://developer.dell.com/hybrid-integrations/securelink/bizflows/edit/U-HAUL_PC_856_ANS_MAPPING_OB"
    assert session._is_sso_transition_url(hip) is False  # "ping" in MAPPING stopped the BizFlow Edit page as HIP_AUTH_SESSION_EXPIRED
    assert session._is_sso_transition_url("https://host/app/author/shipping") is False
    for sso in ("https://myaccess.dell.com/x", "https://idp.dell.com/idp/SSO.saml2", "https://sso.dell.com/as/authorization.oauth2",
                "https://www.dell.com/signin?x", "https://host/auth/realms/x", "https://login.microsoftonline.com/common/oauth2/authorize"):
        assert session._is_sso_transition_url(sso) is True, sso


def test_cli_backend_and_control_center_wiring():
    from typer.testing import CliRunner

    from hip_id_agent.cli import app
    from hip_id_agent.config import AppConfig

    assert AppConfig().edit_sections.form_wait_seconds == 30.0 and AppConfig().edit_sections.block_read_only_changes is True
    result = CliRunner().invoke(app, ["learn-edit-sections", "--help"])
    assert result.exit_code == 0 and "Edit section" in result.output
    backend = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    assert '"edit_sections": _edit_sections_summary(cfg)' in backend and "/api/learning/edit-sections" in backend
    for ui in (ROOT / "webui" / "app.js", ROOT / "backend" / "webui" / "app.js"):
        assert "editSectionsMetric" in ui.read_text(encoding="utf-8")
