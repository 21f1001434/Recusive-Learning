"""V243R24: the model is chosen once by live task performance; Document Type
operations go through the row's expander, as on the live portal.

Model selection: once, during the live GO/NO-GO run (or the first live mission
page), every available text model answers the same questions about the real
Document Types listing -- which control opens row X's Edit / Migrate (its
expander), which control searches, which one opens + Add -- and is scored
against the page itself.  The most accurate model is locked and every later
call uses it.

Row operations (screenshots of 2026-09-27): the Document Types listing has no
action button on the row.  Search, click the row's chevron, and the expanded
details show the environment tabs (DEV / TEST1 / TEST2 / PROD), the Version and
Edit / Clone / Migrate.  Migrate opens a menu of target environments.  Edit /
Clone open a drawer pre-filled from the record.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter
from hip_id_agent.model_qualification import (
    SCREEN_JS, build_questions, ensure_model_qualification, load_selection, qualify_models,
)
from hip_id_agent.portal_operations import run_portal_operations
from hip_id_agent.portal_skills import PortalSkillStore
from hip_id_agent.task_operations import task_operation_specs
from doctypes_listing_support import PHASE, DocTypesPortal
from loader_portal_support import patch_navigation, real_session_config
from phase_replica_support import chromium_path

CONFIRM = "ALLOW HIP MUTATION"


# ---------------------------------------------------------------- helpers
async def _screen(url: str, tmp: Path) -> Dict[str, Any]:
    cfg = real_session_config(tmp, loading_seconds=20)
    session = BrowserSession(cfg, tmp / "screen")
    await session.start()
    patch_navigation(session)
    try:
        await session.goto_base_and_complete_sso(url)
        from hip_id_agent.model_qualification import capture_screen

        return await capture_screen(session.page, wait_seconds=10)
    finally:
        await session.close()


@pytest.fixture(scope="module")
def listing_screen(tmp_path_factory):
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    with DocTypesPortal() as portal:
        return asyncio.run(_screen(portal.url, tmp_path_factory.mktemp("screen")))


class _Client:
    """Dell AIA stand-in: each model answers with a fixed skill level."""

    def __init__(self, skill: Dict[str, float], delay: Dict[str, float] | None = None):
        self.skill, self.delay, self.calls = skill, delay or {}, []

    def autogen_reply(self, system: str, task: str, model: str | None = None) -> str:
        import time

        self.calls.append(model)
        time.sleep(self.delay.get(str(model), 0.0))
        level = self.skill.get(str(model), 0.0)
        if level < 0:
            raise RuntimeError("deployment unavailable")
        payload = json.loads(task)
        answers = {}
        for i, q in enumerate(payload["questions"]):
            right = _Client.expected[q["id"]]
            answers[q["id"]] = right if (i + 1) <= round(level * len(payload["questions"])) else 9999
        return json.dumps({"answers": answers})


def _answers(screen: Dict[str, Any]) -> Dict[str, Any]:
    """The right answer of every qualification question (R29: also the completion judgments)."""
    from hip_id_agent.model_qualification import qualification_questions

    return {q["id"]: (q["expected"][0] if isinstance(q["expected"], list) else q["expected"]) for q in qualification_questions(screen)}


def _router(tmp: Path, client: _Client, models: List[str]) -> OnPremModelPortfolioRouter:
    base = AppConfig().model_portfolio.model_dump()
    base.update(text_models=models, availability_probe_enabled=False, record_usage_ledger=False)
    return OnPremModelPortfolioRouter(tmp / "memory" / "model_portfolio", SimpleNamespace(**base),
                                      aia_config=SimpleNamespace(model="gpt-oss-120b"), client=client)


# ------------------------------------------------ questions from the live page
def test_the_questions_are_answered_by_the_page_itself(listing_screen):
    controls = {c["id"]: c for c in listing_screen["controls"]}
    questions = build_questions(listing_screen)
    kinds = [q["kind"] for q in questions]
    assert kinds.count("row_action_entry") == 3 and {"search", "create", "next_page"} <= set(kinds)
    for q in questions:
        picked = [controls[i] for i in q["expected"]]
        if q["kind"] == "row_action_entry":
            # The first click to reach a Document Type's Edit / Migrate is its expander.
            assert all(c["name"] == "Expand the row" and c["row"] in q["question"] for c in picked), q
        if q["kind"] == "search":
            assert picked[0]["placeholder"] == "Table search"
        if q["kind"] == "create":
            assert picked[0]["name"] == "+ Add"


def test_every_model_gets_the_same_task_and_the_most_accurate_one_is_selected(listing_screen, tmp_path):
    _Client.expected = _answers(listing_screen)
    client = _Client({"gpt-oss-120b": 1.0, "llama-3-3-70b-instruct": 0.75, "gpt-oss-20b": 0.4, "mistral-small-3-1-24b-instruct-2503": -1})
    router = _router(tmp_path, client, ["gpt-oss-20b", "gpt-oss-120b", "llama-3-3-70b-instruct", "mistral-small-3-1-24b-instruct-2503"])
    result = qualify_models(router, listing_screen, source="test")
    assert sorted(client.calls) == sorted(router.available_models("text"))  # one identical task each
    ranking = {r["model"]: r for r in result["ranking"]}
    assert result["status"] == "selected" and result["selected_model"] == "gpt-oss-120b"
    assert ranking["gpt-oss-120b"]["accuracy"] == 1.0 and ranking["gpt-oss-20b"]["qualified"] is False
    assert ranking["mistral-small-3-1-24b-instruct-2503"]["error"]
    assert result["fallback_order"] == ["llama-3-3-70b-instruct"]


def test_a_faster_model_wins_a_tie_on_accuracy(listing_screen, tmp_path):
    _Client.expected = _answers(listing_screen)
    client = _Client({"gpt-oss-120b": 1.0, "gpt-oss-20b": 1.0}, delay={"gpt-oss-120b": 0.4})
    result = qualify_models(_router(tmp_path, client, ["gpt-oss-120b", "gpt-oss-20b"]), listing_screen, source="test")
    assert result["selected_model"] == "gpt-oss-20b"  # same score, answered faster: chosen by performance


def test_qualification_runs_once_and_the_selection_is_used_for_every_call(listing_screen, tmp_path, monkeypatch):
    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)
    _Client.expected = _answers(listing_screen)
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    client = _Client({"gpt-oss-120b": 0.75, "llama-3-3-70b-instruct": 1.0})
    router = _router(tmp_path, client, ["gpt-oss-120b", "llama-3-3-70b-instruct"])

    class _Page:
        async def evaluate(self, script):
            assert script == SCREEN_JS
            return listing_screen

    first = asyncio.run(ensure_model_qualification(cfg, _Page(), source="live_go_live", router=router, run_dir=tmp_path / "run"))
    assert first["status"] == "selected" and first["selected_model"] == "llama-3-3-70b-instruct"
    assert load_selection(cfg)["locked"] is True and (tmp_path / "run" / "model_qualification.json").is_file()
    calls = len(client.calls)
    again = asyncio.run(ensure_model_qualification(cfg, _Page(), source="first_live_mission", router=router))
    assert again["status"] == "already_qualified" and len(client.calls) == calls  # one time

    # Every later call uses the selected model -- learning and complex tasks too.
    fresh = _router(tmp_path, client, ["gpt-oss-120b", "llama-3-3-70b-instruct"])
    assert os.environ["HIP_MODEL_ROUTER_SELECTED_TEXT"] == "llama-3-3-70b-instruct"
    assert fresh.default_text_model() == "llama-3-3-70b-instruct"
    assert fresh.select_candidates("action_selection", learning=True, complex_task=True, force_multi_model=True) == ["llama-3-3-70b-instruct"]
    assert fresh.manifest()["qualification"]["selected_model"] == "llama-3-3-70b-instruct"
    # Proven down: the next model that passed the same qualification takes over.
    fresh.state["availability"] = {"llama-3-3-70b-instruct": {"available": False, "checked_epoch": __import__("time").time()}}
    assert fresh.qualified_model() == "gpt-oss-120b"
    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)


def test_the_live_go_no_go_run_qualifies_the_models():
    from hip_id_agent import cli, live_runtime_certification
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow, FullDummyFillOptions
    import backend.app as backend

    source = inspect.getsource(live_runtime_certification.certify_live_runtime)
    assert "qualify_models_on_live_page(" in source and '"model_qualification"' in source
    assert "requalify_models" in inspect.signature(live_runtime_certification.certify_live_runtime).parameters
    assert "requalify_models" in backend.LiveRuntimeCertificationRequest.model_fields
    assert hasattr(cli, "qualify_models_cmd")
    # Fallback: the first live mission page qualifies once when certification never ran.
    assert FullDummyFillOptions().qualify_models_on_first_page is False
    assert "qualify_models_on_live_page(" in inspect.getsource(FullDummyFillE2EFlow.run)
    assert "qualify_models_on_first_page=" in inspect.getsource(cli.run_full_dummy_fill)


# ------------------------------------------------------------ request parsing
@pytest.mark.parametrize("task,panel,values", [
    ("migrate document type Abbvie_SRC_DocType_IN from DEV to TEST2", {"environment": "DEV"}, {"target_environment": "TEST2"}),
    ("migrate the document type Abbvie_TRGT_Doctype version 1.0 to TEST1", {"version": "1.0"}, {"target_environment": "TEST1"}),
    ("edit the DEV version 1.0 of document type Abbvie_TRGT_Doctype and save", {"environment": "DEV", "version": "1.0"}, None),
    ("edit document type Abbvie_TRGT_Doctype in TEST1", {"environment": "TEST1"}, None),
])
def test_the_environment_tab_version_and_target_come_from_the_request(task, panel, values):
    (spec,) = task_operation_specs(task)
    assert spec.get("panel") == panel and spec.get("values") == values


# ------------------------------------------------------- real browser: operations
async def _operate(tmp: Path, portal: DocTypesPortal, runs: List[Any], *, allow: bool = True, extra: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    cfg = real_session_config(tmp, loading_seconds=20)
    reports = []
    for n, run in enumerate(runs, start=1):
        session = BrowserSession(cfg, tmp / f"run{n}")
        await session.start()
        patch_navigation(session)
        try:
            specs = [s for task in (run if isinstance(run, list) else [run])
                     for s in (task_operation_specs(task) if isinstance(task, str) else [task])]
            reports.append(await run_portal_operations(
                cfg, {"operations": specs, **(extra or {})}, run_dir=tmp / f"ops{n}", allow_portal_mutation=allow,
                confirmation=CONFIRM if allow else "", browser=session, listing_urls={PHASE: portal.url}))
        finally:
            await session.close()
    return reports


def _needs_chromium():
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


def test_the_live_listing_has_no_row_actions_only_the_expander(tmp_path):
    _needs_chromium()
    with DocTypesPortal() as portal:
        screen = asyncio.run(_screen(portal.url, tmp_path))
    in_rows = [c for c in screen["controls"] if c["row"]]
    assert in_rows and {c["name"] for c in in_rows} == {"Expand the row"}


def test_migrate_expands_the_row_picks_the_target_and_confirms_once(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with DocTypesPortal(confirm_migrate=True) as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, ["migrate document type Abbvie_SRC_DocType_IN from DEV to TEST2"]))
        record, posts = portal.find("Abbvie_SRC_DocType_IN"), list(portal.posts)
    op = report["operations"][0]
    assert op["result"] == "SUCCESS" and op["status"] == "committed_and_verified", op
    assert op["open"]["path"] == ["expand row", "tab:DEV", "migrate"]
    assert op["open"]["panel"]["verified_by"] == "name_field"  # the details are that row's
    assert op["open"]["menu_items"] == ["TEST1", "TEST2"] and op["choice"] == "TEST2"
    assert op["commit"]["confirmation"]["label"] == "migrate"
    assert posts == [{"path": "/api/doctypes/migrate", "body": {"name": "Abbvie_SRC_DocType_IN", "from": "DEV", "to": "TEST2", "version": "1.0"}}]
    assert record["envs"]["TEST2"] == ["1.0"]
    learned = PortalSkillStore(tmp_path / "memory").opener_plan(PHASE, "migrate")
    assert learned["expander"] is True and learned["path"][0] == "expand row"
    assert {"role": "button", "name": "Expand the row"} .items() <= learned["selectors"][0].items()
    assert not any(k in json.dumps(learned) for k in ("\"x\"", "\"y\"", "coordinates"))


def test_migrate_never_clicks_when_the_target_holds_the_version_or_is_not_offered_or_the_name_is_ambiguous(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with DocTypesPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, [[
            "migrate document type Abbvie_SRC_DocType_IN from DEV to TEST1",   # TEST1 already holds 1.0
            "migrate document type Abbvie_SRC_DocType_IN to PROD",             # not offered from DEV
            "migrate document type Abbvie_SRC to TEST1",                       # two rows match
        ]]))
        posts = list(portal.posts)
    existing, not_offered, ambiguous = report["operations"]
    assert existing["result"] == "EXISTING" and existing["open"]["target_check"]["target_versions"] == ["1.0"]
    assert not_offered["result"] == "NEEDS_INPUT" and not_offered["needs_input"]["offered"] == ["TEST1", "TEST2"]
    assert ambiguous["result"] == "NEEDS_INPUT" and len(ambiguous["needs_input"]["offered"]) == 2
    assert posts == []


def test_migrate_is_refused_without_the_mutation_gate(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.delenv("HIP_ALLOW_PORTAL_MUTATION", raising=False)
    with DocTypesPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, ["migrate document type Abbvie_TRGT_Doctype to TEST1"], allow=False))
        posts = list(portal.posts)
    op = report["operations"][0]
    assert op["result"] == "BLOCKED" and op["status"] == "commit_not_authorized" and op["choice"] == "TEST1"
    assert posts == []


def test_edit_changes_only_the_requested_fields_of_the_chosen_environment_and_version(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    edit = {"phase": PHASE, "operation": "edit", "target": "Abbvie_TRGT_Doctype", "panel": {"environment": "DEV", "version": "1.0"},
            "values": {"description": "Abbvie target doctype (edited by agent)", "transaction_type": "850"}, "commit": True}
    same = {"phase": PHASE, "operation": "edit", "target": "Abbvie_TRGT_Doctype_IN",
            "values": {"description": "Abbvie_TRGT_Doctype_IN", "transaction_type": "XML"}, "commit": True}
    with DocTypesPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, [[edit, same]]))
        posts = list(portal.posts)
    changed, unchanged = report["operations"]
    assert changed["result"] == "SUCCESS" and changed["open"]["path"] == ["expand row", "tab:DEV", "version:1.0", "edit"]
    assert [(c["field"], c["current"], c["requested"], c["change"]) for c in changed["changes"]] == [
        ("transaction_type", "XML", "850", True), ("description", "Abbvie_TRGT_Doctype", "Abbvie target doctype (edited by agent)", True)]
    assert changed["unrelated_changes"] == [] and changed["after"]["all_seen"] is True
    (post,) = posts
    saved = post["body"]["values"]
    assert saved["transactionType"] == "850" and saved["value"] == "PurchaseOrder" and len(saved["attributes"]) == 2  # the rest kept
    assert unchanged["result"] == "EXISTING" and unchanged["status"] == "no_change_needed"


def test_an_edit_that_changed_an_unrequested_field_is_not_saved(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    edit = {"phase": PHASE, "operation": "edit", "target": "Abbvie_TRGT_Doctype", "values": {"transaction_type": "856"}, "commit": True}
    with DocTypesPortal(clear_description_on_transaction_type=True) as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, [edit]))
        posts = list(portal.posts)
    op = report["operations"][0]
    assert op["status"] == "unrelated_field_changed" and op["result"] == "FAILED"
    assert op["unrelated_changes"] == [{"field": "description", "before": "Abbvie_TRGT_Doctype", "after": ""}]
    assert posts == []


def test_clone_keeps_the_source_configuration_and_needs_a_new_name(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    clone = {"phase": PHASE, "operation": "clone", "target": "Abbvie_TRGT_Doctype",
             "values": {"name": "Abbvie_TRGT_Doctype_V2", "description": "Clone of Abbvie_TRGT_Doctype"}, "commit": True}
    with DocTypesPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, [[clone, "clone document type Abbvie_TRGT_Doctype and save"]]))
        created, posts = portal.find("Abbvie_TRGT_Doctype_V2"), list(portal.posts)
    cloned, nameless = report["operations"]
    assert cloned["result"] == "SUCCESS" and cloned["open"]["path"] == ["expand row", "clone"]
    assert created["transactionType"] == "XML" and created["details"]["DEV"]["1.0"]["identifierValue"] == "PurchaseOrder"
    assert nameless["result"] == "NEEDS_INPUT" and nameless["needs_input"]["field"] == "name"
    assert len(posts) == 1


def test_a_verified_action_path_is_promoted_from_exploration_to_deterministic(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with DocTypesPortal(confirm_migrate=False) as portal:
        first, second = asyncio.run(_operate(tmp_path, portal, [
            "migrate document type Abbvie_997_DocType to TEST1", "migrate document type Abbvie_TRGT_Doctype to TEST1"]))
    assert first["operations"][0]["learned_mode"] == "exploration"
    assert second["operations"][0]["learned_mode"] == "deterministic"
    assert second["operations"][0]["open"]["learned_mode"] == "exploration"  # what it knew when it started
    store = PortalSkillStore(tmp_path / "memory")
    assert store.summary(PHASE)["action_paths"]["migrate"]["mode"] == "deterministic"
    assert store.verify_opener(PHASE, "edit") is False  # nothing learned for Edit yet to verify


def test_deploy_of_a_document_type_uses_migrate_the_portals_way_to_reach_an_environment(tmp_path, monkeypatch):
    _needs_chromium()
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    with DocTypesPortal() as portal:
        (report,) = asyncio.run(_operate(tmp_path, portal, ["deploy the document type Abbvie_997_DocType to TEST2"]))
        record = portal.find("Abbvie_997_DocType")
    op = report["operations"][0]
    assert op["result"] == "SUCCESS" and op["open"]["path"] == ["expand row", "migrate"] and op["choice"] == "TEST2"
    assert record["envs"]["TEST2"] == ["1.0"]
