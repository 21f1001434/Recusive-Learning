"""V243R29: an exactly filled form completes the phase; all models re-validated, champion chosen.

Request (2026-09-29): "it should be able to understand that everything has been
filled correctly and committed according to the input.json and complete that
task or phase. It needs to revalidate all the models and then choose the
champion."

Before R29 a phase whose live form already held every input.json value could
stay open: a text/vision model judge that answered "not complete" blocked it,
the champion/challenger panel (since R24 only the one locked model) agreed, and
the phase was held for a human; a newly learned phase always waited for a human
even when exact; an attempt that failed after the form was complete reopened
and refilled it.  The locked model had been chosen once on listing-navigation
questions only -- never on judging whether a form is complete.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hip_id_agent.config import AppConfig

PHASE = "source_document_type"


# ------------------------------------------------------------ real browser
def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


def _doc_data() -> Dict[str, Any]:
    from phase_replica_support import ROOT

    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict(payload["objects"][PHASE])
    obj["attributes_to_configure"] = obj["attributes_to_configure"][:1]
    return {"objects": {PHASE: obj}}


def test_the_live_form_proof_counts_only_committed_input_json_values(tmp_path: Path):
    """Filled exactly -> proven; a value only typed into a dropdown search reverts; a cleared one is named."""
    _need_browser()
    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.input_json_authority import prove_input_json_completion
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph
    from loader_portal_support import LoaderPortal, patch_navigation, real_session_config

    data = _doc_data()

    async def run():
        with LoaderPortal(stuck_loads=0, attribute_rows=1) as portal:
            session = BrowserSession(real_session_config(tmp_path), tmp_path / "run")
            await session.start()
            session._active_phase_name = PHASE
            patch_navigation(session)
            try:
                await session.goto_base_and_complete_sso(portal.url)
                page = await session._ensure_active_page(portal.url)
                filled = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, PHASE), phase=PHASE, input_data=data, config=None,
                    output_dir=tmp_path / "out", max_cycles=2, executor=execute_document_type_state_graph)
                exact = await prove_input_json_completion(page=page, phase=PHASE, phase_input=data)
                # Type into Data Format Type's search box without choosing anything.
                dft = page.locator("dds-dropdown", has=page.locator("label", has_text="Data Format Type")).locator("input").first
                await dft.click()
                await dft.fill("EDI")
                typed = await prove_input_json_completion(page=page, phase=PHASE, phase_input=data)
                # The portal clears Description (a text field input.json owns).
                await page.evaluate("""() => { const t = Array.from(document.querySelectorAll('textarea,input'))
                    .find(e => (e.placeholder || '').toLowerCase().includes('description') || (e.name || '') === 'description');
                    t.value = ''; t.dispatchEvent(new Event('input', {bubbles: true})); t.dispatchEvent(new Event('change', {bubbles: true})); }""")
                cleared = await prove_input_json_completion(page=page, phase=PHASE, phase_input=data)
                return filled, exact, typed, cleared
            finally:
                await session.close()

    filled, exact, typed, cleared = asyncio.run(run())
    assert filled["pass"] is True
    assert exact["pass"] is True and exact["authoritative"] is True and exact["matched_count"] >= 8
    assert exact["read_only"] is True and exact["form_mutated"] is False and exact["values_stored"] is False
    assert typed["pass"] is True  # the typed search text reverted: only the committed XML counts
    assert cleared["pass"] is False and any("description" in f.lower() for f in cleared["missing_fields"])


# ------------------------------------------------------------ the decision
def _judge(*, text_pass: bool, vision_pass: bool = True, deterministic: bool = True) -> Dict[str, Any]:
    return {
        "pass": bool(text_pass and vision_pass and deterministic), "status": "blocked",
        "deterministic_judge": {"pass": deterministic},
        "text_model_judge": {"status": "ok", "pass": text_pass, "model": "mistral-small-3-1-24b-instruct-2503"},
        "vision_model_judge": {"status": "ok", "pass": vision_pass, "model": "llama-4-vision"},
    }


def test_an_exact_live_form_completes_the_phase_despite_a_model_judge(tmp_path: Path):
    from hip_id_agent.input_json_authority import accept_exact_phase, write_authority

    proof = {"pass": True, "authoritative": True, "matched_count": 12, "matched_fields": ["data_format_type"],
             "rule": "every input.json value ... committed", "read_only": True, "source": "current_live_browser",
             "deterministic_pass": True, "live_reproof_schema": "hip.live-read-only-phase-reproof.v1"}
    judge = _judge(text_pass=False)
    record = write_authority(tmp_path, proof, reason="after_section_judge", judge_result=judge)
    assert record["model_judges_overruled"] == [{"judge": "text", "model": "mistral-small-3-1-24b-instruct-2503", "pass": False}]
    diagnosis = {"reasons": [{"code": "TEXT_JUDGE_BLOCKED"}]}
    accepted, remaining, overruled = accept_exact_phase(judge, diagnosis, record)
    assert overruled is True and remaining == {} and accepted["pass"] is True
    assert accepted["status"] == "pass_input_json_exact" and accepted["pre_authority_pass"] is False
    assert accepted["overruled_diagnosis"]["reasons"][0]["code"] == "TEXT_JUDGE_BLOCKED"
    # The proof is now the phase's exact checkpoint (the mission's existing no-replay path).
    from hip_id_agent.dummy_fill_e2e import phase_exact_completion_checkpoint

    assert phase_exact_completion_checkpoint(PHASE, tmp_path)["pass"] is True


def test_a_form_that_is_not_exact_is_never_passed_by_the_authority():
    from hip_id_agent.input_json_authority import accept_exact_phase, is_authoritative, learning_review_needed

    judge = _judge(text_pass=False)
    diagnosis = {"reasons": [{"code": "X"}]}
    for proof in (
        {"pass": False, "missing_fields": ["description"]},
        {"pass": True, "deterministic_pass": True, "read_only": True, "source": "current_live_browser", "matched_fields": []},
        {"pass": True, "deterministic_pass": True, "read_only": True, "source": "current_live_browser",
         "matched_fields": ["a"], "required_upload_proof": {"pass": False}},
    ):
        assert not is_authoritative(proof)
        kept, remaining, overruled = accept_exact_phase(judge, diagnosis, {"pass": is_authoritative(proof)})
        assert overruled is False and remaining == diagnosis and kept["pass"] is False
    # A newly learned phase asks a human only when the form is not already exact.
    assert learning_review_needed(learning_phase=True, authority={"pass": True}) is False
    assert learning_review_needed(learning_phase=True, authority={"pass": False}) is True
    assert learning_review_needed(learning_phase=True, authority={"pass": True}, review_even_when_exact=True) is True
    assert learning_review_needed(learning_phase=False, authority={"pass": False}) is False


def test_the_mission_uses_the_live_form_as_the_answer():
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    judged = source.index("judge_result = phase_judge.judge_artifact_section(")
    authority = source.index('reason="after_section_judge"')
    assert judged < authority < source.index("model_consensus = judge_consensus.resolve(")
    assert "accept_exact_phase(judge_result, diagnosis, input_authority)" in source
    assert 'not input_authority.get("pass") and bool(getattr(self.config.human_in_the_loop, "multi_model_judge_on_disagreement"' in source
    assert "and not review_skipped_exact" in source and '"acceptance_source": "input_json_exact_authority"' in source
    # A failed attempt proves the live form before anything reopens it.
    error_at = source.index('reason=f"attempt_{attempt_no}_error"')
    assert error_at < source.index("recovered_reporting_only = bool(")
    cfg = AppConfig().human_in_the_loop
    assert cfg.input_json_exact_is_authoritative is True and cfg.review_learning_phase_even_when_exact is False


# ------------------------------------------------ re-validate all, champion
class _Client:
    """Dell AIA stand-in: navigation and judgment skill per model."""

    answers: Dict[str, Any] = {}

    def __init__(self, nav: Dict[str, float], judge: Dict[str, float]):
        self.nav, self.judge, self.calls = nav, judge, []

    def autogen_reply(self, system: str, task: str, model: str | None = None) -> str:
        self.calls.append(model)
        questions = json.loads(task)["questions"]
        navq = [q for q in questions if not q["id"].startswith("j")]
        judq = [q for q in questions if q["id"].startswith("j")]
        out = {}
        for group, skill in ((navq, self.nav.get(model, 0.0)), (judq, self.judge.get(model, 0.0))):
            good = round(skill * len(group))
            for i, q in enumerate(group):
                right = _Client.answers[q["id"]]
                if i < good:
                    out[q["id"]] = right
                elif isinstance(right, dict):
                    out[q["id"]] = {"match": not right["match"], "fields": []}
                else:
                    out[q["id"]] = 9999
        return json.dumps({"answers": out})


def _screen() -> Dict[str, Any]:
    return json.loads((Path(__file__).parent / "fixtures" / "doctypes_listing_screen.json").read_text(encoding="utf-8"))


def _router(tmp: Path, client: _Client, models: List[str]):
    from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter

    base = AppConfig().model_portfolio.model_dump()
    base.update(text_models=models, availability_probe_enabled=False, record_usage_ledger=False)
    return OnPremModelPortfolioRouter(tmp / "memory" / "model_portfolio", SimpleNamespace(**base),
                                      aia_config=SimpleNamespace(model=models[0]), client=client)


def _cfg(tmp: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp / "memory")
    return cfg


class _Page:
    def __init__(self, screen):
        self.screen = screen

    async def evaluate(self, script):
        return self.screen

    async def wait_for_timeout(self, ms):
        return None


def test_the_judgment_questions_come_from_the_live_table():
    from hip_id_agent.model_qualification import build_judge_questions, score_answers

    questions = build_judge_questions(_screen())
    assert len(questions) == 4 and [q["expected"]["match"] for q in questions] == [True, False, True, False]
    changed = [q["expected"]["fields"][0] for q in questions if not q["expected"]["match"]]
    assert len(set(changed)) == 2  # a different column each time
    for q in questions:
        row = next(r for r in _screen()["rows"] if r["name"] == q["row"])
        live = dict(zip(_screen()["headers"], row["cells"]))
        differs = [k for k, v in q["record"].items() if live.get(k) != v]
        assert differs == q["expected"]["fields"]  # the right answer is read from the page
    right = {q["id"]: q["expected"] for q in questions}
    assert score_answers(json.dumps({"answers": right}), questions)["judge_accuracy"] == 1.0
    wrong = {q["id"]: {"match": True} for q in questions}
    assert score_answers(json.dumps({"answers": wrong}), questions)["judge_accuracy"] == 0.5


def test_every_model_is_revalidated_and_the_champion_must_also_judge(tmp_path: Path, monkeypatch):
    """An old (v1) lock is re-validated once; a model that navigates well but judges badly is not the champion."""
    from hip_id_agent.model_qualification import (
        QUALIFICATION_VERSION, SCHEMA, ensure_model_qualification, load_selection, qualification_questions,
        revalidation_reason, selection_path)
    from hip_id_agent.safe_io import safe_write_json

    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)
    cfg = _cfg(tmp_path)
    safe_write_json(selection_path(cfg), {"schema_version": SCHEMA, "status": "selected", "locked": True,
                                          "selected_model": "mistral-small-3-1-24b-instruct-2503", "correct": 8, "total": 8,
                                          "qualified_at": "2026-09-27T10:00:00+00:00", "fallback_order": []})
    assert revalidation_reason(load_selection(cfg), cfg) == "qualification_version_upgrade"
    _Client.answers = {q["id"]: (q["expected"][0] if isinstance(q["expected"], list) else q["expected"])
                       for q in qualification_questions(_screen())}
    client = _Client(nav={"mistral-small-3-1-24b-instruct-2503": 1.0, "gpt-oss-120b": 0.67, "llama-3-3-70b-instruct": 1.0},
                     judge={"mistral-small-3-1-24b-instruct-2503": 0.25, "gpt-oss-120b": 1.0, "llama-3-3-70b-instruct": 0.75})
    models = ["mistral-small-3-1-24b-instruct-2503", "gpt-oss-120b", "llama-3-3-70b-instruct"]
    router = _router(tmp_path, client, models)
    result = asyncio.run(ensure_model_qualification(cfg, _Page(_screen()), source="live_go_live", router=router))
    assert sorted(client.calls) == sorted(models)  # every model asked the same questions
    ranking = {r["model"]: r for r in result["ranking"]}
    assert ranking["mistral-small-3-1-24b-instruct-2503"]["qualified"] is False  # navigates, but judges 1/4
    assert result["status"] == "selected" and result["selected_model"] == "llama-3-3-70b-instruct"
    assert result["judge_correct"] == 3 and result["judge_total"] == 4
    assert result["revalidation"]["reason"] == "qualification_version_upgrade"
    assert result["revalidation"]["previous_model"] == "mistral-small-3-1-24b-instruct-2503"
    lock = load_selection(cfg)
    assert lock["qualification_version"] == QUALIFICATION_VERSION and lock["selected_model"] == "llama-3-3-70b-instruct"
    calls = len(client.calls)
    again = asyncio.run(ensure_model_qualification(cfg, _Page(_screen()), source="first_live_mission", router=router))
    assert again["status"] == "already_qualified" and len(client.calls) == calls  # once per version


def test_a_champion_that_keeps_judging_against_the_live_form_is_revalidated(tmp_path: Path, monkeypatch):
    from hip_id_agent.model_qualification import (
        QUALIFICATION_VERSION, SCHEMA, ensure_model_qualification, load_selection, qualification_questions,
        record_live_judge_truth, revalidation_reason, selection_path)
    from hip_id_agent.safe_io import safe_write_json

    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)
    cfg = _cfg(tmp_path)
    from hip_id_agent.models import utc_now

    safe_write_json(selection_path(cfg), {"schema_version": SCHEMA, "qualification_version": QUALIFICATION_VERSION,
                                          "status": "selected", "locked": True, "selected_model": "gpt-oss-120b",
                                          "correct": 10, "total": 10, "qualified_at": utc_now(), "fallback_order": []})
    wrong = {"text_model_judge": {"status": "ok", "pass": False, "model": "gpt-oss-120b"}}
    right = {"text_model_judge": {"status": "ok", "pass": True, "model": "gpt-oss-120b"}}
    assert record_live_judge_truth(cfg, right, truth=True, phase="data_map")["revalidation_due"] == ""
    for phase in ("source_document_type", "rule"):
        assert record_live_judge_truth(cfg, wrong, truth=True, phase=phase)["revalidation_due"] == ""
    third = record_live_judge_truth(cfg, wrong, truth=True, phase="biz_flow")
    assert third["wrong"] == 3 and third["correct"] == 1
    assert revalidation_reason(load_selection(cfg), cfg).startswith("champion_judged_against_the_live_form")
    # The next qualification asks every model again and chooses the champion anew.
    _Client.answers = {q["id"]: (q["expected"][0] if isinstance(q["expected"], list) else q["expected"])
                       for q in qualification_questions(_screen())}
    client = _Client(nav={"gpt-oss-120b": 1.0, "llama-3-3-70b-instruct": 1.0}, judge={"gpt-oss-120b": 0.25, "llama-3-3-70b-instruct": 1.0})
    router = _router(tmp_path, client, ["gpt-oss-120b", "llama-3-3-70b-instruct"])
    result = asyncio.run(ensure_model_qualification(cfg, _Page(_screen()), source="first_live_mission", router=router))
    assert result["selected_model"] == "llama-3-3-70b-instruct" and not revalidation_reason(load_selection(cfg), cfg)


def test_a_revalidation_that_cannot_finish_keeps_the_champion(tmp_path: Path):
    from hip_id_agent.model_qualification import SCHEMA, ensure_model_qualification, load_selection, selection_path
    from hip_id_agent.safe_io import safe_write_json

    cfg = _cfg(tmp_path)
    safe_write_json(selection_path(cfg), {"schema_version": SCHEMA, "status": "selected", "locked": True,
                                          "selected_model": "mistral-small-3-1-24b-instruct-2503", "correct": 8, "total": 8,
                                          "qualified_at": "2026-09-27T10:00:00+00:00", "fallback_order": []})
    router = _router(tmp_path, _Client(nav={}, judge={}), ["gpt-oss-120b"])
    empty = {"url": "/x", "heading": "", "controls": [], "headers": [], "rows": []}
    result = asyncio.run(ensure_model_qualification(cfg, _Page(empty), source="live_go_live", router=router, wait_seconds=0))
    assert result["status"] == "kept_previous_selection" and result["locked"] is True
    assert result["selected_model"] == "mistral-small-3-1-24b-instruct-2503"
    assert load_selection(cfg)["selected_model"] == "mistral-small-3-1-24b-instruct-2503"


def test_certification_mission_cli_and_control_center_revalidate():
    from hip_id_agent import cli, live_runtime_certification as lrc
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    assert "revalidation_reason(existing, cfg)" in inspect.getsource(lrc.qualify_models_on_live_page)
    assert "Model champion chosen by live task performance (all models validated)" in inspect.getsource(lrc.certify_live_runtime)
    assert "revalidation_reason(current, self.config)" in inspect.getsource(FullDummyFillE2EFlow.run)
    assert "revalidation_reason(current, cfg)" in inspect.getsource(cli.qualify_models_cmd)
    root = Path(__file__).resolve().parents[1]
    for base in ("webui", "backend/webui"):
        js = (root / base / "app.js").read_text(encoding="utf-8")
        assert "judged ${q.judge_correct||0}/${q.judge_total}" in js and "re-validation due" in js
    mp = AppConfig().model_portfolio
    assert mp.qualification_judge_questions == 4 and mp.qualification_min_judge_accuracy == 0.5
    assert mp.qualification_max_age_days == 30 and mp.qualification_revalidate_after_judge_errors == 3


def test_the_text_judge_records_which_model_judged():
    from hip_id_agent.section_judge import DualModelSectionJudge

    judge = DualModelSectionJudge.__new__(DualModelSectionJudge)
    judge.aia = SimpleNamespace(json_decision=lambda system, task: {"pass": False}, _model=lambda: "gpt-oss-120b")
    assert judge._text_judge({"x": 1}) == {"status": "ok", "model": "gpt-oss-120b", "pass": False}
