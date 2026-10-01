"""V243R35: the live agent chat on the right of the Control Center.

"It should also be like a live chat interaction on the right, with the live
actions the agent is taking -- what it clicks and what is happening."

* Every action of a real fill is narrated, in order, by the form's own labels:
  the page it opens, each dropdown it opens, the option it selects, the value it
  types, each field it verifies, the phase finishing -- secrets masked, no
  selectors -- and the fill is no slower for it.
* The operator talks back: status, what's left, pause / resume / stop, phase
  review answers and hints.  A pause holds the agent between fields, and paused
  time counts against neither the no-progress watchdog nor the wall budget.
* Hints reach the agent (it acknowledges them) and the form planner, advisory only.
* A live frame of the browser is captured read-only for the panel.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TP = "source_transport_profile"


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():
        pytest.skip("Chromium is not available")


def _chat(run_dir: Path) -> List[Dict[str, Any]]:
    path = run_dir / "agent_chat.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def _replica():
    from phase_replica_support import replica_html

    return replica_html("transport_profile_full_dds.html").replace(
        "duplicateMessage: 'Transport Profile already exists in DEV environment.'", "")


@pytest.fixture(autouse=True)
def _clean_chat_state():
    from hip_id_agent import agent_chat, operator_control

    yield
    agent_chat.deactivate()
    operator_control.unbind()


def test_the_chat_narrates_every_action_of_a_real_fill_by_the_forms_labels(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent import agent_chat, operator_control
    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.mission_trace import MissionTraceLedger
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import chromium_path, uhaul_input

    data = uhaul_input(TP, tmp_path)
    run = tmp_path / "run"
    trace = MissionTraceLedger(run, run_id="r35", phases=[TP])
    agent_chat.activate(trace.chat)
    operator_control.bind(run)
    trace.mark_phase(TP, status="running", attempt=1, activity="Filling the form")

    async def fill():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                await page.set_content(_replica())
                await page.wait_for_timeout(300)
                started = time.monotonic()
                result = await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data,
                    config=None, output_dir=tmp_path / "out", max_cycles=2)
                return result, time.monotonic() - started
            finally:
                await browser.close()

    result, seconds = asyncio.run(fill())
    trace.mark_phase(TP, status="completed", attempt=1)
    trace.finalize(complete=True)
    assert result["pass"] is True and seconds < 60  # narration costs nothing noticeable (~28 s)

    rows = _chat(run)
    texts = [r["text"] for r in rows]
    joined = "\n".join(texts)
    # In order: the phase, then field by field -- what it opens, selects, types, verifies.
    for expected in [
        "▶ Working on Source Transport Profile: Filling the form",
        "Filling System Type with “Dell Application”",
        "Opened the System Type dropdown",
        "Selected “Dell Application” in System Type",
        "✓ System Type = “Dell Application” — verified on the form",
        "Filling System Name with “AIC - DCE”",  # the form's label for partner_name
        "Typed “SFTP_U-HAUL_ASN_PC_SRC_IB” into Profile Name",
        "Selected “SFTP HAFT” in Interface Type",
        "Filling Existing Account with “Yes”",  # a radio is named by its question
        "Chose “Yes” for Existing Account",
        "Selected “Move To Archive” in Post Transfer Action",  # the portal's spelling, as clicked
        "✅ Source Transport Profile is complete",
        "🏁 Mission complete — 1/1 phases done",
    ]:
        assert expected in texts, (expected, joined)
    positions = [texts.index(t) for t in ("Filling System Type with “Dell Application”", "Opened the System Type dropdown",
                                          "Selected “Dell Application” in System Type",
                                          "✓ System Type = “Dell Application” — verified on the form")]
    assert positions == sorted(positions)
    assert sum(1 for r in rows if r["kind"] == "verified") == 15
    assert all(r["seq"] == i for i, r in enumerate(rows, start=1))
    assert all(r["phase"] == TP for r in rows if r["kind"] in {"field", "click", "select", "type", "verified"})
    # Observability, not internals: no selectors in the chat.
    assert "dds-radio" not in joined and "input#" not in joined and "#dds" not in joined


def test_secrets_are_masked_and_selectors_become_names():
    from hip_id_agent import agent_chat

    class Page:
        pass

    run = Path(__file__).parent  # never written: no active feed
    assert agent_chat.say("nothing active") == {}
    assert agent_chat.display_value("password", "hunter2") == agent_chat.MASK
    assert agent_chat.display_value("Profile Name", "X" * 200).endswith("…”")
    assert agent_chat._target_text("input[name=profileName]") == "Profile Name"
    assert agent_chat._target_text('role=button[name="Create"]') == "Create"
    assert agent_chat._target_text("xpath=//div[3]/span") == ""
    assert agent_chat._short_url("https://hip.dell.com/transport-profiles/create?token=abc#x") == "hip.dell.com/transport-profiles/create"
    del run


def test_broker_and_session_lines(tmp_path: Path):
    from hip_id_agent import agent_chat
    from hip_id_agent.models import ActionEvent

    class Page:
        pass

    feed = agent_chat.activate(agent_chat.AgentChatFeed(tmp_path))
    page = Page()
    agent_chat.executor_step(page, phase=TP, node={"node_id": "n1", "field_key": "client_secret", "expected_value": "s3cr3t"}, stage="start")
    agent_chat.broker_action(page, action="fill", label="HIP Portal text field (tp)", success=True, value="s3cr3t")
    agent_chat.executor_step(page, phase=TP, node={"node_id": "n2", "field_key": "interface_type", "expected_value": "SFTP HAFT"}, stage="start")
    agent_chat.broker_action(page, action="click", label="HIP Portal DDS combobox", success=True)
    agent_chat.broker_action(page, action="search", label="HIP Portal DDS combobox search", success=True, value="SFTP")
    agent_chat.broker_action(page, action="click", label="HIP Portal DDS option SFTP HAFT", success=False, error="HIP_SEMANTIC_TARGET_DRIFT")
    agent_chat.broker_action(page, action="click", label="HIP Portal tab Connection", success=True)
    agent_chat.session_action(ActionEvent(action_id="a1", type="navigate", target="https://hip.dell.com/tp?x=1",
                                          page_url_after="https://hip.dell.com/tp?x=1"), phase=TP)
    agent_chat.session_action(ActionEvent(action_id="a2", type="click", target="#create-btn"), phase=TP, label="Create")
    agent_chat.session_action(ActionEvent(action_id="a3", type="click", target="#x"), phase=TP, label="Inner", in_broker=True)
    agent_chat.session_action(ActionEvent(action_id="a4", type="fill", target="input[name=password]", value_redacted="***MASKED***",
                                          was_secret=True), phase=TP)
    agent_chat.session_action(ActionEvent(action_id="a5", type="wait", target="portal_ready"), phase=TP)
    agent_chat.session_action(ActionEvent(action_id="a6", type="screenshot", target="x"), phase=TP)
    agent_chat.deactivate(feed)
    texts = [r["text"] for r in _chat(tmp_path)]
    assert texts == [
        f"Typed {agent_chat.MASK} into Client Secret",
        "Opened the Interface Type dropdown",
        "Typed “SFTP” to search Interface Type",
        "✗ Could not do it: selected “SFTP HAFT” in Interface Type (HIP_SEMANTIC_TARGET_DRIFT)",
        "Switched to the “Connection” tab",
        "Opened hip.dell.com/tp",
        "Clicked “Create”",
        f"Typed {agent_chat.MASK} into “Password”",
        "Waiting for the portal to finish loading",
    ]
    assert "s3cr3t" not in (tmp_path / "agent_chat.jsonl").read_text(encoding="utf-8")


def test_a_pause_holds_the_agent_between_fields_and_the_watchdog_does_not_count_it(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent import agent_chat, operator_control
    from hip_id_agent.autonomous_form_runtime import execute_autonomous_phase_goal
    from hip_id_agent.mission_trace import MissionTraceLedger
    from hip_id_agent.phase_progress import run_with_progress_watchdog
    from hip_id_agent.stateful_form_runtime import compile_phase_state_graph
    from phase_replica_support import chromium_path, uhaul_input

    data = uhaul_input(TP, tmp_path)
    run = tmp_path / "run"
    trace = MissionTraceLedger(run, run_id="r35p", phases=[TP])
    agent_chat.activate(trace.chat)
    operator_control.bind(run, poll_seconds=0.1)

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                await page.set_content(_replica())
                await page.wait_for_timeout(300)

                async def marker():
                    progress = getattr(page, "_hip_executor_progress", None) or {}
                    return {"signature": "same", "executor_progress": str(progress.get("token") or "")}

                async def operator():
                    # Pause as soon as the second field starts, hold 8 s (longer than the
                    # 4 s no-progress limit), then resume.
                    while "Filling System Name" not in "".join(r["text"] for r in _chat(run)):
                        await asyncio.sleep(0.05)
                    operator_control.write_control(run, "paused")
                    await asyncio.sleep(8.0)
                    operator_control.write_control(run, "running")

                fill = execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(data, TP), phase=TP, input_data=data,
                    config=None, output_dir=tmp_path / "out", max_cycles=2)
                watched = run_with_progress_watchdog(
                    fill, phase=TP, marker_provider=marker, checkpoint_provider=lambda: {"pass": False},
                    no_progress_seconds=4.0, poll_seconds=0.25, refill_probe_seconds=0, refill_loop_seconds=0)
                result, _ = await asyncio.gather(watched, operator())
                return result
            finally:
                await browser.close()

    result = asyncio.run(scenario())
    assert result["pass"] is True  # the watchdog (4 s) never fired during the 8 s pause
    assert operator_control.paused_seconds() >= 7.5
    texts = [r["text"] for r in _chat(run)]
    paused = next(i for i, t in enumerate(texts) if t.startswith("⏸ Paused before"))
    resumed = next(i for i, t in enumerate(texts) if t.startswith("▶ Resuming before"))
    # Held between fields: the field in hand finished, nothing happened while paused.
    assert texts[paused - 1].startswith("✓ System Name = ")
    assert resumed == paused + 1 and texts[paused] == "⏸ Paused before Profile Name. Say “resume” to continue."
    assert texts[resumed + 1].startswith("Filling Profile Name")


def test_paused_time_does_not_count_against_the_wall_budget(tmp_path: Path):
    from hip_id_agent import operator_control
    from hip_id_agent.phase_progress import run_with_progress_budget

    operator_control.bind(tmp_path, poll_seconds=0.05)

    async def marker():
        return {"progress_units": 0}

    async def budget(seconds_paused: float):
        async def operator():
            if seconds_paused:
                operator_control.write_control(tmp_path, "paused")
                await asyncio.sleep(seconds_paused)
                operator_control.write_control(tmp_path, "running")

        async def work():
            await asyncio.sleep(2.0)
            return "done"

        result, _ = await asyncio.gather(
            run_with_progress_budget(work(), phase=TP, budget_seconds=1.0, marker_provider=marker,
                                     extend=lambda units: {}, max_finalize_extensions=0),
            operator())
        return result

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(budget(0.0))  # 2 s of work in a 1 s budget
    assert asyncio.run(budget(1.6)) == "done"  # 1.6 s of it paused: only ~0.4 s counted


def test_a_pause_counts_in_full_even_if_the_agent_never_looked_during_it(tmp_path: Path):
    from hip_id_agent import operator_control

    operator_control.write_control(tmp_path, "paused")
    operator_control.write_control(tmp_path, "running")
    operator_control.bind(tmp_path)  # pauses before this mission do not count
    assert operator_control.paused_seconds() == 0.0
    operator_control.write_control(tmp_path, "paused")
    time.sleep(0.6)
    operator_control.write_control(tmp_path, "running")  # paused and resumed between two reads
    time.sleep(0.25)
    assert 0.55 <= operator_control.paused_seconds() < 1.0
    assert operator_control.is_paused() is False
    row = operator_control.read_control(tmp_path)
    assert row["state"] == "running" and row["paused_at_epoch"] is None and row["paused_total_seconds"] >= 0.6
    operator_control.unbind()
    assert operator_control.paused_seconds() == 0.0 and operator_control.notes() == []


def _api_config(tmp_path: Path) -> Path:
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["reporting"]["memory_dir"] = str(tmp_path / "memory")
    data["reporting"]["runs_dir"] = str(tmp_path / "runs")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_operator_talks_to_the_agent_through_the_control_center(tmp_path: Path):
    from fastapi.testclient import TestClient

    from backend.app import app
    from hip_id_agent import agent_chat, operator_control
    from hip_id_agent.config import load_config
    from hip_id_agent.human_phase_review import human_phase_review_from_config
    from hip_id_agent.mission_trace import MissionTraceLedger

    cfg_path = _api_config(tmp_path)
    run = tmp_path / "runs" / "RUN_R35"
    trace = MissionTraceLedger(run, run_id="RUN_R35", phases=[TP])
    trace.mark_phase(TP, status="running", attempt=1, activity="Filling the form")
    trace.chat.post("Selected “SFTP HAFT” in Interface Type", kind="select", phase=TP)
    (run / "input_json_live_map.json").write_text(json.dumps({
        "phase": TP, "phase_display": "Source Transport Profile", "total": 3, "exact": 1, "complete": False,
        "rows": [{"field": "interface_type", "label": "Interface Type", "expected": "SFTP HAFT", "live": "SFTP HAFT", "status": "exact"},
                 {"field": "profile_name", "label": "Profile Name", "expected": "P1", "live": "X", "status": "different"},
                 {"field": "existing_account", "label": "Existing Account", "expected": "Yes", "live": "", "status": "not_on_screen"},
                 {"field": "is_compression_required", "label": "Is Compression Required", "expected": "FALSE", "status": "not_checked"}],
    }), encoding="utf-8")
    (run / "agent_chat").mkdir()
    (run / "agent_chat" / "live_frame.jpg").write_bytes(b"\xff\xd8\xff\xe0JFIF-frame")
    q = {"config": str(cfg_path)}
    client = TestClient(app)

    first = client.get("/api/mission/chat", params=q).json()
    assert first["found"] and first["run_id"] == "RUN_R35"
    assert [m["text"] for m in first["messages"]] == ["▶ Working on Source Transport Profile: Filling the form",
                                                      "Selected “SFTP HAFT” in Interface Type"]
    st = first["state"]
    assert st["running"] is True and st["phase"] == TP and st["phase_display"] == "Source Transport Profile"
    assert st["live_map"]["exact"] == 1 and st["paused"] is False and st["review"] is None
    assert first["frame_url"].startswith("/api/mission/chat/frame?")
    frame = client.get(first["frame_url"])
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"

    # Incremental: only what is new after the cursor.
    trace.chat.post("Opened the Profile Name field", kind="click", phase=TP)
    nxt = client.get("/api/mission/chat", params={**q, "cursor": first["cursor"]}).json()
    assert [m["text"] for m in nxt["messages"]] == ["Opened the Profile Name field"]

    def say(text):
        return client.post("/api/mission/chat", json={"text": text, "config": str(cfg_path)}).json()

    status = say("what are you doing?")
    assert status["intent"] == "status"
    assert "Working on Source Transport Profile." in status["reply"] and "1/3 values on the form" in status["reply"]
    assert "Last thing I did: Opened the Profile Name field" in status["reply"]
    left = say("what's left?")
    assert left["intent"] == "left" and "2 of 3 values still to finish" in left["reply"]
    assert "Profile Name → “P1” (shows “X”)" in left["reply"] and "Existing Account → “Yes” (not on screen yet)" in left["reply"]

    paused = say("pause")
    assert paused["intent"] == "pause" and operator_control.read_control(run)["state"] == "paused"
    assert client.get("/api/mission/chat", params=q).json()["state"]["paused"] is True
    resumed = say("resume")
    assert resumed["intent"] == "resume" and operator_control.read_control(run)["state"] == "running"

    # A hint: the backend notes it; the agent picks it up at its next safe point.
    operator_control.bind(run)
    hint = say("Interface Type is on the Connection tab")
    assert hint["intent"] == "hint" and "input.json stays the source" in hint["reply"]
    agent_chat.activate(trace.chat)
    asyncio.run(operator_control.checkpoint(phase=TP, where="Profile Name"))
    assert operator_control.notes() == ["Interface Type is on the Connection tab"]
    assert any(m["text"].startswith("📝 Got your note: “Interface Type is on the Connection tab”") for m in _chat(run))

    assert "Nothing is waiting for your review" in say("accept")["reply"]
    store = human_phase_review_from_config(load_config(cfg_path))
    review = store.create_or_update(run_id="RUN_R35", phase=TP, phase_display="Source Transport Profile", attempt=1,
                                    automated_judge={"pass": True}, verification={}, exact_checkpoint={"pass": True},
                                    model_consensus={})
    assert client.get("/api/mission/chat", params=q).json()["state"]["review"]["request_id"] == review["request_id"]
    accepted = say("looks correct")
    assert accepted["intent"] == "accept" and accepted["reply"].startswith("✅ Recorded")
    assert store.pending(run_id="RUN_R35") == []

    stop = say("stop")
    assert stop["intent"] == "stop" and "no mission process started from the Control Center" in stop["reply"]
    assert "pause" in say("help")["reply"]

    # The whole conversation, both sides, in order.
    convo = client.get("/api/mission/chat", params=q).json()["messages"]
    users = [m["text"] for m in convo if m["role"] == "user"]
    assert users == ["what are you doing?", "what's left?", "pause", "resume", "Interface Type is on the Connection tab",
                     "accept", "looks correct", "stop", "help"]
    assert sum(1 for m in convo if m.get("source") == "operator" and m["role"] == "agent") == len(users)


def test_the_planner_gets_the_hints_as_advice_only():
    from hip_id_agent import autonomous_form_runtime, llm_form_planner

    assert llm_form_planner._compact_state({"operator_notes": ["it is on the Connection tab"], "other": 1}) == {
        "operator_notes": ["it is on the Connection tab"]}
    assert "operator_notes" in llm_form_planner.FORM_PLANNER_SYSTEM and "never authorize" in llm_form_planner.FORM_PLANNER_SYSTEM
    assert '"operator_notes": operator_control.notes()' in inspect.getsource(autonomous_form_runtime)


def test_self_heal_and_watchdog_say_what_they_do(tmp_path: Path):
    from hip_id_agent import agent_chat
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealDecision, _say_self_heal

    feed = agent_chat.activate(agent_chat.AgentChatFeed(tmp_path))
    _say_self_heal(RuntimeSelfHealDecision(phase=TP, attempt=1, failure_kind="exception", classification="whitelabel_error_page",
                                           signature="s", action="restart_browser_session", retry=True, action_success=True,
                                           reason="", evidence_dir=""))
    _say_self_heal(RuntimeSelfHealDecision(phase=TP, attempt=3, failure_kind="exception", classification="portal_loading_stuck",
                                           signature="s", action="stop_fail_closed", retry=False, action_success=False,
                                           reason="HIP_PORTAL_LOADING_STUCK_AFTER_RECOVERY: check the portal", evidence_dir=""))
    agent_chat.deactivate(feed)
    texts = [r["text"] for r in _chat(tmp_path)]
    assert texts[0] == ("♻ Self-heal (whitelabel error page): closing the browser, opening a fresh one and starting "
                        "this stage again; then I continue from input.json")
    assert texts[1].startswith("⛔ Could not recover automatically (portal loading stuck): HIP_PORTAL_LOADING_STUCK_AFTER_RECOVERY")


def test_the_live_frame_is_captured_without_touching_the_page(tmp_path: Path):
    _need_browser()
    from playwright.async_api import async_playwright

    from hip_id_agent import agent_chat
    from phase_replica_support import chromium_path

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            page = await browser.new_page(viewport={"width": 800, "height": 500})
            try:
                await page.set_content("<input id=a value=x><script>window.__m=0;new MutationObserver(r=>window.__m+=r.length)"
                                       ".observe(document,{subtree:true,childList:true,attributes:true});</script>")
                await page.focus("#a")
                task = asyncio.ensure_future(agent_chat.live_frame_loop(lambda: page, tmp_path, seconds=0.5))
                await asyncio.sleep(1.8)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return await page.evaluate("window.__m")
            finally:
                await browser.close()

    mutations = asyncio.run(run())
    frame = tmp_path / "agent_chat" / "live_frame.jpg"
    assert frame.read_bytes()[:3] == b"\xff\xd8\xff"  # a JPEG
    assert mutations == 0  # no style injected, nothing for the agent's DOM observer to see


def test_the_mission_and_the_control_center_are_wired():
    from hip_id_agent import dummy_fill_e2e
    from hip_id_agent.config import AppConfig, load_config

    source = inspect.getsource(dummy_fill_e2e)
    for needle in ("agent_chat.activate(mission_trace.chat)", "operator_control.bind(root_dir",
                   "agent_chat.live_frame_loop(", "await operator_control.checkpoint(phase=phase",
                   "agent_chat.deactivate(mission_trace.chat)", "Live check: "):
        assert needle in source, needle
    cfg = AppConfig().agent_chat
    assert (cfg.enabled, cfg.live_frame_seconds, cfg.pause_poll_seconds) == (True, 3.0, 0.5)
    assert load_config(ROOT / "config.yaml").agent_chat.enabled is True
    for folder in (ROOT / "webui", ROOT / "backend" / "webui"):
        html = (folder / "index.html").read_text(encoding="utf-8")
        js = (folder / "app.js").read_text(encoding="utf-8")
        css = (folder / "styles.css").read_text(encoding="utf-8")
        for needle in ('id="chatDock"', 'id="chatLog"', 'id="chatFrameImg"', 'id="chatForm"', 'data-chat="pause"', 'id="chatOpenBtn"'):
            assert needle in html, needle
        for needle in ("function initAgentChat", "/api/mission/chat", "initAgentChat();", "function sendChat"):
            assert needle in js, needle
        assert ".chat-dock{" in css and "prefers-reduced-motion" in css
    for name in ("index.html", "app.js", "styles.css"):
        assert (ROOT / "webui" / name).read_bytes() == (ROOT / "backend" / "webui" / name).read_bytes()
