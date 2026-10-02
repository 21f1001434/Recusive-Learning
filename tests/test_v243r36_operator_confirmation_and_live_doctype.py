"""V243R36: a filled Source Document Type finishes -- and the operator is understood.

What the live run showed: the form was filled, the chat said every field was
verified, yet the live map stayed at 0/29, the proof kept saying "Status" was
not on the form, the watchdog stopped the attempt, the recovery ladder threw
the filled form away (refresh, reopen, browser restart) and filled it again;
"everything filled correctly" in the chat was not understood, and the agent
ended with a CDP reconnect error.

* The live-like replica (the Status switch is a ``<button role=switch>`` with no
  value, the portal shell has a permanent navigation menu) is filled once, the
  live map counts it, the proof passes.
* A permanent menu, a chip list or an open accordion no longer make the form
  "busy"; a busy signal that never ends is read through after 20 s.
* A switch is judged by its checked state; a field the judge cannot pair is
  cross-checked with the executor's own reader.
* "Everything is filled correctly" / Accept confirms the phase: the agent stops
  filling and finishes the form on screen; never a field the portal flags.
* A stalled, nearly complete form asks the operator instead of being reopened blank.
* The chat reads messages in context ("status is wrong" is about the Status field).
* Click targets covered by a leftover popup or a toast are cleared; a browser
  restart never waits on a DevTools port the old Chrome still holds.
* Model qualification: a tie on accuracy goes to the more capable model.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = "source_document_type"
TP = "source_transport_profile"

SWITCH = ('<input type="checkbox" role="switch" id="dds-form-field-103" name="status" formcontrolname="status" '
          'checked aria-checked="true"><span>Enabled</span>')
LIVE_SWITCH = ('<button type="button" role="switch" id="dds-form-field-103" name="status" formcontrolname="status" '
               'aria-checked="true" class="dds__switch" onclick="this.setAttribute(\'aria-checked\', '
               'this.getAttribute(\'aria-checked\')===\'true\'?\'false\':\'true\')"><span>Enabled</span></button>')
NAV = ('<nav class="portal-shell" style="position:fixed;left:0;top:0;width:40px;height:100%"><ul role="menu">'
       '<li role="menuitem">Home</li><li role="menuitem">Document Types</li></ul></nav>')


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():
        pytest.skip("Chromium is not available")


def _chat(run_dir: Path) -> List[Dict[str, Any]]:
    path = run_dir / "agent_chat.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


@pytest.fixture(autouse=True)
def _clean_chat_state():
    from hip_id_agent import agent_chat, operator_control

    yield
    agent_chat.deactivate()
    operator_control.unbind()


def _doc_input() -> Dict[str, Any]:
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    return {"objects": {DOC: dict((payload.get("objects") or payload)[DOC])}}


def _live_like_doc_html(data: Dict[str, Any]) -> str:
    html = (ROOT / "tests" / "fixtures" / "document_type_full_dds.html").read_text(encoding="utf-8")
    assert SWITCH in html
    rows = len(data["objects"][DOC]["attributes_to_configure"])
    html = html.replace(SWITCH, LIVE_SWITCH).replace("<body>", "<body>" + NAV, 1)
    return html.replace("<script>", f"<script>window.__attributeRows = {rows};</script><script>", 1)


# ------------------------------------------------------------ the live Document Type
def test_the_live_like_document_type_is_filled_once_and_proven_complete(tmp_path: Path):
    """The live run's form: Status is a value-less switch button, the shell has a permanent menu."""
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.input_json_authority import prove_input_json_completion, quiet_completion_probe
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph
    from phase_replica_support import chromium_path

    data = _doc_input()

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(_live_like_doc_html(data))
            before = await quiet_completion_probe(page=page, phase=DOC, phase_input=data)
            started = time.monotonic()
            result = await execute_autonomous_phase_goal(
                page=page, graph=compile_phase_state_graph(data, DOC), phase=DOC, input_data=data, config=None,
                output_dir=tmp_path / "engine", max_cycles=2, executor=execute_document_type_state_graph)
            seconds = time.monotonic() - started
            after = await quiet_completion_probe(page=page, phase=DOC, phase_input=data)
            proof = await prove_input_json_completion(page=page, phase=DOC, phase_input=data)
            await browser.close()
            return before, result, seconds, after, proof

    before, result, seconds, after, proof = asyncio.run(run())
    # The permanent navigation menu used to make the probe "busy" forever (map frozen at 0/29).
    assert before["status"] == "filling" and before["map"]["total"] >= 20
    assert result["pass"] is True and len(result.get("cycles") or []) == 1, "one pass, no refill"
    assert after["status"] == "complete" and after["pass"] is True
    assert proof["pass"] is True and proof["missing_fields"] == [] and "status" in proof["matched_fields"]
    assert seconds < 150


def test_permanent_menus_lists_and_accordions_do_not_make_the_form_busy(monkeypatch):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent import input_json_authority
    from hip_id_agent.input_json_authority import _BUSY_JS, quiet_completion_probe
    from phase_replica_support import chromium_path

    page_html = (
        NAV
        + '<header><div role="menubar"><button aria-haspopup="menu" aria-expanded="true">User</button>'
          '<ul role="menu"><li role="menuitem">Sign out</li></ul></div></header>'
        + '<main style="margin-left:60px"><button aria-expanded="true" aria-controls="acc1">Identifiers</button>'
          '<section id="acc1">rows</section>'
        + '<ul role="listbox" aria-label="chips"><li role="option">EDI</li></ul>'
        + '<div class="dds__dropdown"><input id="cb" role="combobox" aria-expanded="false" aria-controls="lb">'
          '<ul id="lb" role="listbox" hidden><li role="option">A</li></ul></div></main>'
    )
    monkeypatch.setattr(input_json_authority, "_PERSISTENT_BUSY_SECONDS", 0.5)

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page()
            await page.set_content(page_html)
            quiet = await page.evaluate(_BUSY_JS)
            await page.evaluate("() => { const c = document.getElementById('cb'); c.setAttribute('aria-expanded', 'true');"
                                " document.getElementById('lb').hidden = false; }")
            open_now = await page.evaluate(_BUSY_JS)
            first = await quiet_completion_probe(page=page, phase=TP, phase_input={"objects": {TP: {"profile_name": "P"}}})
            await asyncio.sleep(0.6)
            later = await quiet_completion_probe(page=page, phase=TP, phase_input={"objects": {TP: {"profile_name": "P"}}})
            await browser.close()
            return quiet, open_now, first, later

    quiet, open_now, first, later = asyncio.run(run())
    assert quiet == {"open": 0, "lists": 0, "busy": 0}  # shell menus, an open accordion, a chip list: not busy
    assert open_now["open"] == 1 and open_now["lists"] == 1  # a dropdown open right now is
    assert first["status"] == "busy"
    # A busy signal that never ends is read through (reading touches nothing).
    assert later["status"] in {"filling", "complete"} and later["busy_ignored"]["open"] == 1


def test_a_switch_is_judged_by_its_checked_state():
    from hip_id_agent.section_judge import DualModelSectionJudge, SectionJudgePolicy

    judge = DualModelSectionJudge(SectionJudgePolicy(enabled=True, require_text_model=False, require_vision_model=False,
                                                     max_repairs=0, fail_closed=True))
    expected = {"facts": [{"field": "status", "input_path": "status", "value": "Enabled", "aliases": ["Status"]}]}
    on = {"label": "Status", "role": "switch", "type": "button", "value": "", "checked": True}
    off = dict(on, checked=False)
    passed = judge.deterministic_judge(expected=expected, actual_state={"controls": [on], "visible_text": ""})
    assert passed["pass"] is True and passed["matched_values"][0]["evidence"] == "checked_state"
    failed = judge.deterministic_judge(expected=expected, actual_state={"controls": [off], "visible_text": ""})
    assert failed["pass"] is False and failed["missing_values"][0]["field"] == "status"
    radio = dict(on, role="radio", type="radio")  # a radio is never read as a switch
    assert judge.deterministic_judge(expected=expected, actual_state={"controls": [radio], "visible_text": ""})["pass"] is False


def test_a_value_the_judge_cannot_read_is_cross_checked_with_the_executors_reader():
    """A DDS single-select whose inner input went blank after the menu closed: the
    chip still shows the committed option.  The executor reads the chip (and verified
    the field); the judge read only the blank input and called the value missing."""
    from hip_id_agent.phase_live_reproof import _cross_check_with_executor_reader
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import uhaul_input

    data = uhaul_input(TP)
    node = next(n for n in compile_phase_state_graph(data, TP)["nodes"] if n.get("field_key") == "interface_type")
    fact = {"field": "interface_type", "input_path": node["input_path"], "value": node["expected_value"], "row_index": None}
    control = {"label": "Interface Type *", "semantic_key": "interface_type", "framework_key": "interfaceType",
               "name": "interfaceType", "role": "combobox", "tag": "input", "component_tag": "dds-dropdown",
               "section": node.get("section"), "value": "", "selected_values": [node["expected_value"]],
               "selection_mode": "single", "selector": "#interfaceType", "index": 0}
    deterministic = {"pass": False, "matched_values": [], "missing_values": [{"field": "interface_type"}],
                     "row_issues": [], "failed_attempts": []}
    out, recovered = _cross_check_with_executor_reader(TP, data, {"facts": [fact]}, [control], deterministic)
    assert recovered == ["interface_type"] and out["pass"] is True and out["missing_values"] == []
    assert out["matched_values"][0]["evidence"] == "executor_reader_fresh_read"
    # A chip with another value is not taken for the expected one.
    wrong = dict(control, selected_values=["SFTP"])
    out, recovered = _cross_check_with_executor_reader(TP, data, {"facts": [fact]}, [wrong], deterministic)
    assert recovered == [] and out["pass"] is False


# ------------------------------------------------------------ operator confirmation
def test_the_operators_confirmation_completes_only_a_form_on_screen(tmp_path: Path):
    from hip_id_agent import agent_chat, operator_control

    operator_control.confirm_complete(tmp_path, DOC, text="from an earlier run")
    time.sleep(1.2)
    operator_control.bind(tmp_path)
    feed = agent_chat.activate(agent_chat.AgentChatFeed(tmp_path))
    assert operator_control.confirmed_complete(DOC) is None  # before this mission: ignored
    operator_control.confirm_complete(tmp_path, DOC, text="everything filled correctly")
    operator_control._S.last_read = 0.0
    assert operator_control.confirmed_complete(DOC)["text"] == "everything filled correctly"
    assert operator_control.read_control(tmp_path)["state"] == "running"

    proof = {"pass": False, "deterministic_pass": False, "read_only": True, "source": "current_live_browser",
             "matched_fields": ["document_type_name", "standard"], "missing_fields": ["status"], "row_issue_fields": [],
             "invalid_fields": [], "actual_control_count": 40, "required_upload_proof": {"pass": True}}
    done = operator_control.confirmed_proof(DOC, proof)
    from hip_id_agent.input_json_authority import is_authoritative

    assert done["pass"] is True and done["operator_confirmed_fields"] == ["status"] and is_authoritative(done)
    assert done["status"] == "exact_live_state_operator_confirmed"
    operator_control.confirmed_proof(DOC, proof)
    said = [r["text"] for r in _chat(tmp_path) if "You confirmed" in r["text"]]
    assert said == ["✅ You confirmed this phase is correct. I proved every other value myself; recorded as confirmed by you: status."]
    # Never over a field the portal flags, never without the form on screen.
    flagged = operator_control.confirmed_proof(DOC, dict(proof, invalid_fields=["Version"]))
    assert flagged["pass"] is False and "Version" in flagged["operator_confirmation_refused"]
    gone = operator_control.confirmed_proof(DOC, dict(proof, actual_control_count=0))
    assert gone["pass"] is False and gone["operator_confirmation_refused"].startswith("the form of this phase is not on screen")
    # A confirmation typed while many values are still empty does not finish the phase:
    # the agent names them and keeps filling; the confirmation stays valid.
    early = operator_control.confirmed_proof(DOC, dict(proof, missing_fields=["status", "version", "standard", "direction"]))
    assert early["pass"] is False and early["operator_confirmation_refused"].startswith("4 input.json values are not on the form yet")
    assert operator_control.confirmed_proof(DOC, dict(proof, missing_fields=["status", "version", "standard"]))["pass"] is True
    warned = [r["text"] for r in _chat(tmp_path) if r["text"].startswith("I can't finish this phase on your confirmation yet")]
    assert len(warned) == 3 and "the portal flags Version" in warned[0] and "(status, version, standard, direction)" in warned[2]
    assert operator_control.confirmed_proof(TP, proof)["pass"] is False  # another phase was not confirmed
    agent_chat.deactivate(feed)


def test_the_watchdog_stops_filling_when_the_operator_confirms(tmp_path: Path):
    from hip_id_agent import operator_control
    from hip_id_agent.phase_progress import PhaseNoProgressError, run_with_progress_watchdog

    operator_control.bind(tmp_path, poll_seconds=0.05)
    ticks = {"n": 0}

    async def filling_forever():
        while True:
            await asyncio.sleep(0.01)

    async def marker():
        ticks["n"] += 1
        if ticks["n"] == 4:
            operator_control.confirm_complete(tmp_path, DOC, text="everything is filled correctly")
        return {"signature": f"s{ticks['n']}", "progress_units": 20}

    async def checkpoint():
        return operator_control.confirmed_proof(DOC, {
            "pass": False, "missing_fields": ["status"], "matched_fields": ["a"], "actual_control_count": 30, "invalid_fields": []})

    async def run():
        return await run_with_progress_watchdog(
            filling_forever(), phase=DOC, marker_provider=marker, checkpoint_provider=checkpoint,
            no_progress_seconds=60, poll_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(PhaseNoProgressError) as info:
        asyncio.run(run())
    assert info.value.code == "HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL"
    assert info.value.payload["stop_reason"] == "operator_confirmed" and time.monotonic() - started < 10


def test_an_unprovable_confirmation_does_not_tear_the_attempt_down(tmp_path: Path):
    from hip_id_agent import agent_chat, operator_control
    from hip_id_agent.phase_progress import run_with_progress_watchdog

    operator_control.bind(tmp_path, poll_seconds=0.05)
    feed = agent_chat.activate(agent_chat.AgentChatFeed(tmp_path))
    operator_control.confirm_complete(tmp_path, DOC, text="done")

    async def filling():
        await asyncio.sleep(0.8)
        return "finished"

    ticks = {"n": 0}

    async def marker():
        ticks["n"] += 1
        return {"signature": f"s{ticks['n']}", "progress_units": ticks["n"]}

    async def checkpoint():  # the portal flags a field: the confirmation cannot complete it
        return operator_control.confirmed_proof(DOC, {"pass": False, "missing_fields": ["status"], "matched_fields": ["a"],
                                                      "actual_control_count": 30, "invalid_fields": ["Version"]})

    async def run():
        return await run_with_progress_watchdog(filling(), phase=DOC, marker_provider=marker, checkpoint_provider=checkpoint,
                                                no_progress_seconds=60, poll_seconds=0.05)

    assert asyncio.run(run()) == "finished"
    agent_chat.deactivate(feed)
    assert any("I can't finish this phase on your confirmation yet" in r["text"] for r in _chat(tmp_path))


def test_a_kept_form_needs_no_route_and_no_refill():
    from hip_id_agent.dummy_fill_e2e import _kept_form_preflight, _kept_form_summary

    proof = {"status": "exact_live_state_operator_confirmed", "matched_count": 28, "operator_confirmed_fields": ["status"]}
    pre = _kept_form_preflight(DOC, 2, proof)
    assert pre["pass"] is True and pre["route"]["pass"] is True and pre["dual_mcp"]["pass"] is True
    assert pre["kept_exact_live_form"]["operator_confirmed_fields"] == ["status"]
    summary = asyncio.run(_kept_form_summary(DOC, proof))
    assert summary["browser_replay_performed"] is False and summary["form_reopened"] is False and summary["pass"] is True


def test_a_stalled_nearly_complete_form_asks_the_operator_instead_of_reopening(tmp_path: Path):
    from hip_id_agent.dummy_fill_e2e import _near_complete_question

    def proof(**extra):
        row = {"reason": "attempt_2_error", "pass": False, "read_only": True, "source": "current_live_browser",
               "matched_fields": [f"f{i}" for i in range(27)], "missing_fields": ["status"], "row_issue_fields": [],
               "invalid_fields": [], "actual_control_count": 60, "required_upload_proof": {"pass": True}}
        row.update(extra)
        (tmp_path / "input_json_completion_authority.json").write_text(json.dumps(row), encoding="utf-8")

    stall = "HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field for 90s"
    ask = lambda message=stall, cls="phase_stall": _near_complete_question(  # noqa: E731
        DOC, tmp_path, message, cls, max_missing=2, reason="attempt_2_error")
    proof()
    question = ask()
    assert question["unread_fields"] == ["status"] and question["matched_count"] == 27
    assert question["question"].startswith("❓ The Source Document Type form holds 27 of 28 input.json values")
    assert "Is the form correct?" in question["question"] and question["reason"].startswith("HIP_PHASE_NEAR_COMPLETE_OPERATOR_QUESTION")
    # A broken page still goes straight to recovery.
    assert ask("HIP_PORTAL_LOADING_STUCK: loader for 60s; HIP_PHASE_NO_PROGRESS_WATCHDOG") == {}
    assert ask(cls="whitelabel_error_page") == {} and ask(cls="authentication_expired") == {}
    assert ask("RuntimeError: something else") == {}
    proof(missing_fields=["status", "version", "standard"])
    assert ask() == {}  # too much missing: refill
    proof(invalid_fields=["Version"])
    assert ask() == {}  # the portal flags a field: repair
    proof(reason="attempt_1_error")
    assert ask() == {}  # a proof from another attempt
    proof(actual_control_count=0)
    assert ask() == {}  # no form on screen


def test_the_mission_asks_before_the_recovery_ladder_and_keeps_the_form():
    from hip_id_agent import dummy_fill_e2e

    source = inspect.getsource(dummy_fill_e2e)
    asked = source.index('proof_reason=f"attempt_{attempt_no}_error"')
    assert asked < source.index("executor_disconnect = shared_browser.is_executor_transport_disconnect(message)")
    assert asked < source.index("decision = await runtime_self_healer.handle_failure(", asked)
    assert 'proof_reason=f"attempt_{attempt_no}_pre_judge_gate"' in source
    assert "if attempt_no > 1 or operator_control.confirmed_complete(phase):" in source
    assert "_kept_form_preflight(phase, attempt_no, kept_exact) if kept_exact else await runtime_self_healer.prepare_phase_attempt(" in source
    assert "question=ask[\"question\"]" in source


# ------------------------------------------------------------ the chat understands
def test_the_chat_reads_the_operators_messages_in_context():
    from hip_id_agent.operator_chat import interpret, interpret_with_model

    for text in ("everything filled correctly", "everything filled correclty", "All fields are filled correctly",
                 "the form is complete", "looks good, move to the next phase", "accept", "it's done correctly"):
        assert interpret(text) == "accept", text
    for text in ("is everything filled correctly?", "it is not filled correctly", "Status is enabled",
                 "Interface Type is on the Connection tab", "status is wrong"):
        assert interpret(text) == "hint", text
    assert interpret("status is wrong", review_pending=True) == "reject"
    assert interpret("status?") == "status" and interpret("what are you doing?") == "status"
    assert interpret("yes", review_pending=True) == "accept" and interpret("yes", paused=True) == "resume"
    assert interpret("yes") == "ack" and interpret("go ahead") == "resume"
    assert interpret("no", review_pending=True) == "reject" and interpret("no") == "ack"
    assert interpret("stop") == "stop" and interpret("pause") == "pause"

    class _Model:
        def __init__(self, answer):
            self.answer, self.calls = answer, 0

        def json_decision(self, system, task):
            self.calls += 1
            return self.answer

    sure = _Model({"intent": "accept", "confidence": 0.93})
    assert interpret_with_model("we're good on this one, wrap it up", client=sure) == {
        "intent": "accept", "source": "model", "confidence": 0.93}
    unsure = interpret_with_model("we're good on this one", client=_Model({"intent": "accept", "confidence": 0.5}))
    assert unsure["intent"] == "hint" and unsure["model_intent"] == "accept"
    assert interpret_with_model("nah, kill it", client=_Model({"intent": "stop", "confidence": 1.0}))["intent"] == "hint"
    exact = _Model({"intent": "reject", "confidence": 1.0})
    assert interpret_with_model("pause", client=exact) == {"intent": "pause", "source": "patterns"} and exact.calls == 0


def _api_config(tmp_path: Path) -> Path:
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["reporting"]["memory_dir"] = str(tmp_path / "memory")
    data["reporting"]["runs_dir"] = str(tmp_path / "runs")
    data.setdefault("aia", {})["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_accept_in_the_chat_or_the_review_panel_confirms_the_phase(tmp_path: Path):
    from fastapi.testclient import TestClient

    from backend.app import app
    from hip_id_agent import operator_control
    from hip_id_agent.config import load_config
    from hip_id_agent.human_phase_review import human_phase_review_from_config
    from hip_id_agent.mission_trace import MissionTraceLedger

    cfg_path = _api_config(tmp_path)
    run = tmp_path / "runs" / "RUN_R36"
    trace = MissionTraceLedger(run, run_id="RUN_R36", phases=[DOC])
    trace.mark_phase(DOC, status="running", attempt=1, activity="Filling the form")
    client = TestClient(app)

    def say(text):
        return client.post("/api/mission/chat", json={"text": text, "config": str(cfg_path)}).json()

    # In the live run this was not understood (and "status" was read as a status question).
    hint = say("Status is enabled, you can see it")
    assert hint["intent"] == "hint" and "confirmations" not in operator_control.read_control(run)
    confirmed = say("everything filled correctly")
    assert confirmed["intent"] == "accept" and "you confirmed Source Document Type is filled correctly" in confirmed["reply"]
    assert operator_control.read_control(run)["confirmations"][DOC]["text"] == "everything filled correctly"

    # The agent's near-complete question, answered in the Phase Review panel.
    store = human_phase_review_from_config(load_config(cfg_path))
    request = store.create_recovery_request(
        run_id="RUN_R36", phase=TP, phase_display="Source Transport Profile", recovery_round=2,
        reason="HIP_PHASE_NEAR_COMPLETE_OPERATOR_QUESTION: …", question="❓ The Source Transport Profile form holds 13 of 14 …")
    state = client.get("/api/mission/chat", params={"config": str(cfg_path)}).json()["state"]
    assert state["review"]["question"].startswith("❓ The Source Transport Profile form holds 13 of 14")
    resolved = client.post("/api/human-phase-review/resolve", json={
        "request_id": request["request_id"], "verdict": "pass", "note": "checked on screen", "config": str(cfg_path)}).json()
    assert resolved["confirmed_phase"] == TP
    assert operator_control.read_control(run)["confirmations"][TP]["text"] == "checked on screen"
    # A rejection confirms nothing.
    again = store.create_recovery_request(run_id="RUN_R36", phase=TP, phase_display="Source Transport Profile",
                                          recovery_round=3, reason="…")
    rejected = client.post("/api/human-phase-review/resolve", json={
        "request_id": again["request_id"], "verdict": "needs_correction", "config": str(cfg_path)}).json()
    assert rejected["confirmed_phase"] == ""


def test_the_semantic_gate_stays_out_of_the_chat(tmp_path: Path):
    from hip_id_agent.mission_trace import MissionTraceLedger

    trace = MissionTraceLedger(tmp_path, run_id="R", phases=[DOC])
    trace.record_observation(DOC, summary="Semantic target SC-1a2b approved for select", source="semantic_action_gate")
    trace.record_observation(DOC, summary="Every input.json value is filled and committed", source="input_json_authority")
    texts = [r["text"] for r in _chat(tmp_path)]
    assert not any("Semantic target" in t for t in texts) and any("Every input.json value" in t for t in texts)


def test_the_control_center_shows_the_agents_question():
    for copy in ("webui", "backend/webui"):
        js = (ROOT / copy / "app.js").read_text(encoding="utf-8")
        assert 'question:"❓"' in js and "st.review.question" in js
        assert "q.selection_reason" in js
        css = (ROOT / copy / "styles.css").read_text(encoding="utf-8")
        assert ".chat-msg.k-question" in css
    assert (ROOT / "webui" / "app.js").read_text(encoding="utf-8") == (ROOT / "backend" / "webui" / "app.js").read_text(encoding="utf-8")


# ------------------------------------------------------------ the browser
def test_a_leftover_popup_is_closed_and_a_toast_is_waited_out_but_a_dialog_is_left_alone():
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent.form_interaction_policy import _clear_interceptor
    from phase_replica_support import chromium_path

    html = """
    <button id="target" style="position:absolute;left:40px;top:40px;width:160px;height:40px">Profile Name</button>
    <ul id="pop" role="listbox" style="position:absolute;left:20px;top:20px;width:220px;height:90px;background:#fff;margin:0">
      <li role="option">A</li></ul>
    <div id="toast" role="alert" style="position:absolute;left:20px;top:20px;width:220px;height:90px;background:#ffd;display:none">Saved</div>
    <div id="dlg" role="dialog" style="position:absolute;left:20px;top:20px;width:220px;height:90px;background:#eef;display:none">Confirm</div>
    <script>
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.getElementById('pop').style.display = 'none'; });
    </script>"""

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page()
            await page.set_content(html)
            target = page.locator("#target")
            popup = await _clear_interceptor(page, target)
            await page.evaluate("() => { const t = document.getElementById('toast'); t.style.display = 'block';"
                                " setTimeout(() => t.style.display = 'none', 600); }")
            toast = await _clear_interceptor(page, target)
            await page.evaluate("() => document.getElementById('dlg').style.display = 'block'")
            dialog = await _clear_interceptor(page, target, wait_ms=400)
            still_open = await page.evaluate("() => getComputedStyle(document.getElementById('dlg')).display")
            await browser.close()
            return popup, toast, dialog, still_open

    popup, toast, dialog, still_open = asyncio.run(run())
    assert popup["pass"] is True and popup["action"] == "escape_popup" and popup["interceptor"]["kind"] == "popup"
    assert toast["pass"] is True and toast["action"] == "wait_transient"
    assert dialog["pass"] is False and dialog["interceptor"]["kind"] == "dialog" and still_open == "block"


class _VersionHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"Browser": "Chrome/140", "Protocol-Version": "1.3",
                           "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.server.server_port}/devtools/browser/x"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _bare_session(tmp_path: Path, port: int):
    from hip_id_agent.browser_session import BrowserSession

    session = object.__new__(BrowserSession)
    session.config = SimpleNamespace(mcp=SimpleNamespace(playwright_mcp_remote_debugging_port=port))
    session._owns_browser_context = True
    session._selected_browser = {"profile": str(tmp_path)}
    session._cdp_port_override = 0
    session._cdp_endpoint = f"http://127.0.0.1:{port}"
    return session


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_the_cdp_endpoint_chrome_really_uses_is_found_and_a_held_port_is_replaced(tmp_path: Path, monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    server = HTTPServer(("127.0.0.1", 0), _VersionHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        dead = _free_port()
        session = _bare_session(tmp_path, dead)
        (tmp_path / "DevToolsActivePort").write_text(f"{server.server_port}\n/devtools/browser/x\n", encoding="utf-8")
        health = asyncio.run(session._probe_cdp_endpoint())
        assert health["ok"] is True and health["endpoint_rediscovered_from"] == "DevToolsActivePort"
        assert session._cdp_endpoint == f"http://127.0.0.1:{server.server_port}"

        # A relaunch: the old Chrome still holds the configured port -> a free one is used.
        held = _bare_session(tmp_path, server.server_port)
        moved = asyncio.run(held._release_or_replace_cdp_port(wait_seconds=0.6))
        assert moved["status"] == "replaced" and moved["held_port"] == server.server_port
        assert held._cdp_port_override == moved["port"] != server.server_port
        free = _bare_session(tmp_path, dead)
        assert asyncio.run(free._release_or_replace_cdp_port(wait_seconds=0.6)) == {"port": dead, "status": "free"}
    finally:
        server.shutdown()
    from hip_id_agent import browser_session

    source = inspect.getsource(browser_session.BrowserSession.restart)
    assert "_release_or_replace_cdp_port" in source


# ------------------------------------------------------------ the model champion
class _QualClient:
    answers: Dict[str, Any] = {}

    def __init__(self, fail_first=(), empty_final=()):
        self.fail_first, self.empty_final, self.calls, self.rest_calls = set(fail_first), set(empty_final), [], []

    def _right(self, task):
        return json.dumps({"answers": {q["id"]: _QualClient.answers[q["id"]] for q in json.loads(task)["questions"]}})

    def autogen_reply(self, system, task, model=None):
        self.calls.append(model)
        if model in self.fail_first and self.calls.count(model) == 1:
            raise TimeoutError("read timed out")
        return "" if model in self.empty_final else self._right(task)

    def chat_rest(self, messages, model=None, **_):
        self.rest_calls.append(model)
        return self._right(messages[-1]["content"])  # the answer from the reasoning channel


def test_a_tie_goes_to_the_more_capable_model_and_a_failed_answer_is_asked_again(tmp_path: Path):
    from hip_id_agent.config import AppConfig
    from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter
    from hip_id_agent.model_qualification import QUALIFICATION_VERSION, qualification_questions, qualify_models

    screen = json.loads((ROOT / "tests" / "fixtures" / "doctypes_listing_screen.json").read_text(encoding="utf-8"))
    _QualClient.answers = {q["id"]: (q["expected"][0] if isinstance(q["expected"], list) else q["expected"])
                           for q in qualification_questions(screen)}
    models = ["gpt-oss-20b", "gpt-oss-120b", "llama-3-3-70b-instruct"]

    def router(client):
        base = AppConfig().model_portfolio.model_dump()
        base.update(text_models=models, availability_probe_enabled=False, record_usage_ledger=False)
        return OnPremModelPortfolioRouter(tmp_path / "memory" / "model_portfolio", SimpleNamespace(**base),
                                          aia_config=SimpleNamespace(model="gpt-oss-120b"), client=client)

    # gpt-oss-120b timed out once and answered right when asked again; all three answer everything.
    client = _QualClient(fail_first={"gpt-oss-120b"}, empty_final={"llama-3-3-70b-instruct"})
    result = qualify_models(router(client), screen, source="test")
    ranking = {r["model"]: r for r in result["ranking"]}
    assert result["selected_model"] == "gpt-oss-120b" and QUALIFICATION_VERSION == 3
    assert ranking["gpt-oss-120b"]["asked"] == 2 and ranking["gpt-oss-120b"]["failed_answers"] == ["read timed out"]
    assert ranking["llama-3-3-70b-instruct"]["answer_read_from"] == "rest_reasoning_fallback"
    assert ranking["gpt-oss-20b"]["why"] == f"same score ({ranking['gpt-oss-20b']['correct']}/{ranking['gpt-oss-20b']['total']}); the champion is the more capable model"
    assert "the most accurate" in result["selection_reason"]
    assert result["fallback_order"] == ["llama-3-3-70b-instruct", "gpt-oss-20b"]

    # More accurate still wins: size never outranks right answers.
    class _Weaker(_QualClient):
        def autogen_reply(self, system, task, model=None):
            text = super().autogen_reply(system, task, model)
            if model == "gpt-oss-120b":
                data = json.loads(text)
                first = sorted(data["answers"])[0]
                data["answers"][first] = 9999 if not isinstance(data["answers"][first], dict) else {"match": None}
                text = json.dumps(data)
            return text

    weaker = qualify_models(router(_Weaker()), screen, source="test")
    assert weaker["selected_model"] == "llama-3-3-70b-instruct"
    assert "the champion answered" in {r["model"]: r for r in weaker["ranking"]}["gpt-oss-120b"]["why"]


def test_r36_settings_and_docs():
    from hip_id_agent.config import AppConfig

    cfg = AppConfig()
    assert cfg.runtime_self_heal.ask_before_reopening_max_missing == 2
    assert cfg.model_portfolio.qualification_retries == 1
    assert cfg.agent_chat.model_intent_fallback is True and cfg.agent_chat.operator_confirmation_max_unread == 3
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert data["runtime_self_heal"]["ask_before_reopening_max_missing"] == 2
    assert data["model_portfolio"]["qualification_retries"] == 1
    assert data["agent_chat"]["model_intent_fallback"] is True and data["agent_chat"]["operator_confirmation_max_unread"] == 3
