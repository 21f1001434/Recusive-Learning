"""V243R23: "deploy / migrate / edit ... the document type X" knows where to click;
a verified phase form can be saved; the phase time budget is larger.

Before: "deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD" was
planned as "open the Partner page, click a page-level Deploy" -- no listing, no
row, no dialog value.  Now such a request becomes a portal operation: the
object's listing, a search for the row, the row's action (or its "More
actions" menu), the action's learned dialog, a governed commit and a listing
check.  The label that opened the action is learned and tried first next time.

The full mission never clicked Save.  ``--save-after-fill`` (Control Center:
"Save each form after it is filled and verified") saves each phase form once it
is filled, every value verified and the judges passed -- through the same
three-part gate, clicked once, reconciled and checked in the listing.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.portal_operations import operation_gate, run_portal_operations, save_verified_phase
from hip_id_agent.portal_skills import PortalSkillStore
from hip_id_agent.task_operations import task_operation_specs
from loader_portal_support import patch_navigation, real_session_config
from operations_portal_support import PHASE, OperationsPortal
from phase_replica_support import chromium_path

CONFIRM = "ALLOW HIP MUTATION"


# ------------------------------------------------------------ request parsing
def test_a_document_type_deploy_request_names_its_listing_row_and_dialog_value():
    assert task_operation_specs("deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD") == [{
        "phase": "source_document_type", "operation": "deploy", "source": "task_box", "commit": True,
        "target": "XML_DellAutoASN_10_U-HAUL_ANS_IB", "values": {"target_environment": "PROD"},
    }]


@pytest.mark.parametrize("task,expected", [
    ("migrate document type XML_DellAutoASN_10_U-HAUL_ANS_IB", [("source_document_type", "migrate", True, "XML_DellAutoASN_10_U-HAUL_ANS_IB")]),
    ("clone the target document type 'XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB'", [("target_document_type", "clone", False, "XML_SHIPMENT_NOTICE_10_U-HAUL_ANS_OB")]),
    ("fill the document type form from input.json and save it", [("source_document_type", "create", True, None)]),
    ("Create the source transport profile from input.json, save it, then deploy it to UAT",
     [("source_transport_profile", "create", True, None), ("source_transport_profile", "deploy", True, None)]),
    ("delete data map named DELLCoXMLASNXX08C_U-HAUL", [("data_map", "delete", True, "DELLCoXMLASNXX08C_U-HAUL")]),
    ("merge business flow U-HAUL_POASN into U-HAUL_POASN_V2", [("biz_flow", "merge", True, "U-HAUL_POASN")]),
])
def test_object_actions_are_recognised(task, expected):
    specs = task_operation_specs(task)
    assert [(s["phase"], s["operation"], s["commit"], s.get("target")) for s in specs] == expected


@pytest.mark.parametrize("task", ["fill document type", "Deploy flow example", "learn data maps", "fill the Data Map"])
def test_plain_fill_and_non_object_requests_keep_their_existing_path(task):
    assert task_operation_specs(task) == []


def test_the_task_box_plan_goes_to_the_document_types_listing_and_its_row_action():
    from fastapi.testclient import TestClient
    import backend.app as backend

    plan = TestClient(backend.app).post("/api/portal-task/plan", json={
        "task": "deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD", "config": "config.yaml",
        "input_json": "examples/uhaul_poasn_full_dummy_input.json",
    }).json()
    assert plan["execution_mode"] == "portal_operation"
    kinds = [s["type"] for s in plan["steps"]]
    assert kinds == ["navigate", "search", "open_row_action", "fill", "commit", "verify_listing"]
    assert plan["steps"][0]["target"].endswith("/securelink/doctypes")
    assert "More actions menu" in plan["steps"][2]["fallbacks"]


# ------------------------------------------------------- real browser: operations
async def _operate(tmp_path: Path, portal: OperationsPortal, runs: List[str], *, allow: bool = True) -> List[Dict[str, Any]]:
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    cfg = real_session_config(tmp_path, loading_seconds=20)
    reports = []
    for n, task in enumerate(runs, start=1):
        session = BrowserSession(cfg, tmp_path / f"run{n}")
        await session.start()
        patch_navigation(session)
        try:
            reports.append(await run_portal_operations(
                cfg, {"operations": task_operation_specs(task)}, run_dir=tmp_path / f"ops{n}",
                allow_portal_mutation=allow, confirmation=CONFIRM if allow else "", browser=session,
                listing_urls={PHASE: portal.url},
            ))
        finally:
            await session.close()
    return reports


def test_a_free_text_deploy_finds_the_row_action_in_its_menu_and_learns_where_it_is(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with OperationsPortal() as portal:
        first, second = asyncio.run(_operate(tmp_path, portal, [
            "deploy the transport profile TP_ALPHA to UAT",
            "deploy the transport profile TP_BETA to UAT",
        ]))
        alpha, beta = portal.find("TP_ALPHA"), portal.find("TP_BETA")
    op1, op2 = first["operations"][0], second["operations"][0]
    assert op1["status"] == "committed_and_verified", op1
    assert op1["open"]["path"] == ["more actions", "deploy"]  # Deploy lives in the row's "More actions" menu
    assert alpha["status"] == "Deployed UAT" and beta["status"] == "Deployed UAT"
    store = PortalSkillStore(tmp_path / "memory")
    assert store.opener_labels(PHASE, "deploy")[0] == "deploy"
    assert op2["status"] == "committed_and_verified"
    assert op2["open"]["learned_opener_labels"][0] == "deploy"  # learned where Deploy is


def test_a_row_action_request_is_refused_without_the_mutation_gate(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    with OperationsPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, ["deploy the transport profile TP_ALPHA to UAT"], allow=False))
        alpha = portal.find("TP_ALPHA")
    assert report["operations"][0]["status"] == "blocked_mutation_authorization"
    assert alpha["status"] == "Draft"


# ------------------------------------------------- save after verified fill
async def _save(tmp_path: Path, portal: OperationsPortal, *, allow: bool) -> Dict[str, Any]:
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    cfg = real_session_config(tmp_path, loading_seconds=20)
    session = BrowserSession(cfg, tmp_path / "run")
    await session.start()
    patch_navigation(session)
    try:
        # A filled, verified phase form is open (here: TP_ALPHA's edit form).
        await session.goto_base_and_complete_sso(portal.url.replace("/tp", "/tp/form?mode=edit&id=TP_ALPHA"))
        await session.page.wait_for_timeout(500)
        return await save_verified_phase(
            cfg, session, phase=PHASE, run_dir=tmp_path / "phase", values={"profile_name": "TP_ALPHA"},
            gate=operation_gate(allow, CONFIRM if allow else ""), listing_url=portal.url,
        )
    finally:
        await session.close()


def test_a_verified_phase_form_is_saved_once_and_checked_in_the_listing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with OperationsPortal() as portal:
        saved = asyncio.run(_save(tmp_path, portal, allow=True))
        posts = list(portal.posts)
    assert saved["status"] == "saved_and_verified", saved
    assert saved["commit"]["label"] == "save" and saved["verification"]["row_found"] is True
    assert PortalSkillStore(tmp_path / "memory").phase_commit_labels(PHASE) == ["save"]
    assert len(posts) == 1  # clicked once


def test_save_after_fill_is_refused_without_the_gate_and_nothing_is_posted(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    with OperationsPortal() as portal:
        saved = asyncio.run(_save(tmp_path, portal, allow=False))
        posts = list(portal.posts)
    assert saved["status"] == "save_not_authorized" and saved["saved"] is False
    assert posts == []


# ------------------------------------------------------- mission and config
def test_the_mission_saves_only_after_verification_and_judges_before_the_handoff():
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions

    assert FullDummyFillOptions().save_after_fill is False  # opt-in
    source = inspect.getsource(FullDummyFillE2EFlow.run)
    save = source.index("save_verified_phase(")
    assert source.index("PHASE_JUDGE_RESULT_FILENAME, judge_result") < save < source.index("mission.mark_phase_complete(phase, attempt=attempt_no")
    assert save < source.index("shared_browser.handoff_to_next_phase(", save)


def test_the_phase_time_budget_is_larger():
    cfg = AppConfig().runtime_self_heal
    assert cfg.max_phase_wall_seconds == 3600 and cfg.progress_extension_seconds == 900 and cfg.max_progress_extensions == 8
    shipped = load_config(Path(__file__).resolve().parents[1] / "config.yaml").runtime_self_heal
    assert shipped.max_phase_wall_seconds == 3600
