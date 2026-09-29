"""V243R27: the model selection survives a slow listing; missions learn from past runs and MLflow.

Live report (2026-09-28): Live GO/NO-GO showed "Model selected by live task
performance (one time)" as WARN with

    HIP_ROUTE_NOT_COMMITTED: ReAct navigation controller could not reach
    .../securelink/doctypes; final observation={... 'target_match': True,
    'target_usable': False, 'logged_in': True, 'same_actual_surface': True ...}

and asked whether the agent learns from old runs / MLflow and improves itself.

Root causes:

* the navigation controller spends one of its 4 steps on every "wait and
  re-observe"; with no loader on screen each wait lasts ~0.5 s, so a route that
  is committed and authenticated but still rendering its listing failed after
  ~2 s (reproduced: 2.3 s);
* a re-run of the qualification that could not finish showed WARN although a
  model had passed the same live task before;
* MLflow 3.x refuses the local run store HIP falls back to unless
  MLFLOW_ALLOW_FILE_STORE=true -- the fail-open tracker switched itself off and
  recorded nothing; nothing ever read MLflow back;
* old runs were imported only as whole-mission pass/fail episodes: phase and
  attempt durations, what stopped them and how long a module needed to render
  were never learned.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hip_id_agent.config import AppConfig

DOCTYPES = "https://developer.dell.com/hybrid-integrations/securelink/doctypes"
PHASE = "source_document_type"


# ------------------------------------------------------------------ helpers
def _session(tmp: Path, *, render_wait: float = 20.0):
    from hip_id_agent.browser_session import BrowserSession
    from loader_portal_support import real_session_config

    cfg = real_session_config(tmp)
    cfg.portal.navigation_render_wait_seconds = render_wait
    session = BrowserSession(cfg, tmp / "run")
    return cfg, session


async def _open(session, url: str) -> Dict[str, Any]:
    await session.start()
    if getattr(session, "semantic_action_gate", None) is not None:
        session.semantic_action_gate.enabled = False
    started = time.monotonic()
    try:
        await session.goto_base_and_complete_sso(url)
        outcome = {"pass": True}
    except Exception as exc:
        outcome = {"pass": False, "error": str(exc)}
    outcome["seconds"] = time.monotonic() - started
    trace = json.loads((session.run_dir / "mcp_runtime" / "navigation_react_trace.json").read_text())
    outcome["actions"] = [s.get("plan", {}).get("action") for s in trace["steps"]]
    outcome["render"] = trace.get("render_wait") or {}
    rows = session.run_dir / "mcp_runtime" / "navigation_render_waits.jsonl"
    outcome["rows"] = [json.loads(x) for x in rows.read_text().splitlines()] if rows.is_file() else []
    return outcome


def _need_browser():
    from phase_replica_support import chromium_path

    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")


# ----------------------------------------------- 1. the route is waited for
def test_a_slowly_rendering_listing_is_waited_for_instead_of_failing(tmp_path: Path):
    _need_browser()
    from doctypes_listing_support import DocTypesPortal

    with DocTypesPortal(boot_delay_ms=5000, live_path=True) as portal:
        cfg, session = _session(tmp_path)

        async def run():
            try:
                return await _open(session, portal.url)
            finally:
                await session.close()

        out = asyncio.run(run())
    assert out["pass"] is True, out.get("error")
    assert "await_route_render" in out["actions"] and out["actions"][-1] == "accept_target"
    assert out["render"]["waited_seconds"] >= 3 and out["render"]["reloaded"] is False
    # Each navigation is recorded for the next missions to learn from.
    assert out["rows"][-1]["module_key"] == "doctypes" and out["rows"][-1]["usable"] is True


def test_a_listing_that_renders_only_after_a_reload_is_reloaded_once(tmp_path: Path):
    _need_browser()
    from doctypes_listing_support import DocTypesPortal

    with DocTypesPortal(live_path=True, stuck_loads=99) as portal:
        cfg, session = _session(tmp_path, render_wait=3.0)
        original = session._execute_react_navigation_action

        async def act(action, target_url):
            if action.get("action") == "reload_target":
                portal.stuck_loads = 0  # this page renders after a reload
            return await original(action, target_url)

        session._execute_react_navigation_action = act

        async def run():
            try:
                return await _open(session, portal.url)
            finally:
                await session.close()

        out = asyncio.run(run())
    assert out["pass"] is True, out.get("error")
    assert out["actions"].count("reload_target") == 1 and out["render"]["reloaded"] is True


def test_a_listing_that_never_renders_fails_bounded_and_says_why(tmp_path: Path):
    _need_browser()
    from doctypes_listing_support import DocTypesPortal

    with DocTypesPortal(live_path=True, stuck_loads=99) as portal:
        cfg, session = _session(tmp_path, render_wait=3.0)

        async def run():
            try:
                return await _open(session, portal.url)
            finally:
                await session.close()

        out = asyncio.run(run())
    assert out["pass"] is False and "HIP_ROUTE_NOT_COMMITTED" in out["error"]
    assert "'reason': 'module_not_rendered'" in out["error"]  # the error now says why
    assert out["actions"].count("reload_target") == 1
    assert out["seconds"] < 60 and out["rows"][-1]["usable"] is False


def test_a_module_that_was_slow_in_a_past_run_is_waited_for_in_the_next_run(tmp_path: Path):
    """The learning loop end to end, in a real browser: record -> learn from the folder -> apply."""
    _need_browser()
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.run_history_learning import apply_lessons, run_history_learner_from_config
    from doctypes_listing_support import DocTypesPortal
    from loader_portal_support import real_session_config

    runs = tmp_path / "runs"

    def config(render_wait: float):
        cfg = real_session_config(tmp_path)
        cfg.portal.navigation_render_wait_seconds = render_wait
        cfg.reporting.runs_dir = str(runs)
        cfg.reporting.memory_dir = str(tmp_path / "memory")
        return cfg

    async def navigate(cfg, run: str, lessons=None):
        session = BrowserSession(cfg, runs / run)
        if lessons:
            apply_lessons(lessons, browser=session)
        try:
            return await _open(session, portal.url)
        finally:
            await session.close()

    with DocTypesPortal(boot_delay_ms=8000, live_path=True) as portal:
        # A past mission: this listing needed ~8 s to render (it had a long render wait).
        past = asyncio.run(navigate(config(30.0), "PAST-RUN"))
        assert past["pass"] is True and past["rows"][-1]["seconds"] >= 7
        (runs / "PAST-RUN" / "mission_state.json").write_text(json.dumps({"mission_status": "complete", "phases": {}}))
        # This mission is configured with a short render wait (2 s).
        cfg = config(2.0)
        control = asyncio.run(navigate(cfg, "WITHOUT-LEARNING"))
        assert control["pass"] is False  # 2 s + one reload is not enough for this listing
        lessons = run_history_learner_from_config(cfg).learn(include_mlflow=False)
        learned = lessons["lessons"]["navigation_render_wait_seconds"]["doctypes"]
        assert learned >= 10  # p95 of the past render time x 1.5
        after = asyncio.run(navigate(cfg, "WITH-LEARNING", lessons))
    assert after["pass"] is True and after["render"]["budget_seconds"] == learned and after["render"]["reloaded"] is False


# --------------------------------------------- 2. model selection on that page
class _Client:
    expected: Dict[str, int] = {}

    def __init__(self, skill: Dict[str, float]):
        self.skill, self.calls = skill, []

    def autogen_reply(self, system: str, task: str, model: str | None = None) -> str:
        self.calls.append(model)
        questions = json.loads(task)["questions"]
        good = round(self.skill.get(str(model), 0.0) * len(questions))
        return json.dumps({"answers": {q["id"]: (_Client.expected.get(q["id"]) if i < good else 9999)
                                       for i, q in enumerate(questions)}})


def _router(tmp: Path, client: _Client, models: List[str]):
    from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter

    base = AppConfig().model_portfolio.model_dump()
    base.update(text_models=models, availability_probe_enabled=False, record_usage_ledger=False)
    return OnPremModelPortfolioRouter(tmp / "memory" / "model_portfolio", SimpleNamespace(**base),
                                      aia_config=SimpleNamespace(model=models[0]), client=client)


def _grade_from_page(monkeypatch):
    import hip_id_agent.model_qualification as mq

    real = mq.qualify_models

    def graded(router, screen, **kwargs):
        _Client.expected = {q["id"]: (q["expected"][0] if isinstance(q["expected"], list) else q["expected"])
                            for q in mq.qualification_questions(screen)}
        return real(router, screen, **kwargs)

    monkeypatch.setattr(mq, "qualify_models", graded)


def test_the_model_is_selected_on_a_slowly_rendering_live_listing(tmp_path: Path, monkeypatch):
    _need_browser()
    import hip_id_agent.dummy_fill_e2e as dfe
    import hip_id_agent.model_portfolio as mp
    from doctypes_listing_support import DocTypesPortal
    from hip_id_agent.live_runtime_certification import _qualification_detail, qualify_models_on_live_page

    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)
    client = _Client({"gpt-oss-120b": 1.0, "llama-3-3-70b-instruct": 0.5})
    router = _router(tmp_path, client, ["gpt-oss-120b", "llama-3-3-70b-instruct"])
    monkeypatch.setattr(mp, "model_portfolio_from_config", lambda config: router)
    _grade_from_page(monkeypatch)
    with DocTypesPortal(boot_delay_ms=5000, live_path=True) as portal:
        monkeypatch.setitem(dfe.PHASE_URLS, PHASE, portal.url)
        cfg, session = _session(tmp_path)

        async def run():
            await session.start()
            session.semantic_action_gate.enabled = False
            cfg.aia.enabled = True  # the stand-in router answers for Dell AIA
            try:
                return await qualify_models_on_live_page(cfg, session, tmp_path / "cert")
            finally:
                await session.close()

        result = asyncio.run(run())
    assert result["status"] == "selected" and result["locked"] is True, result
    assert result["selected_model"] == "gpt-oss-120b" and result["correct"] == result["total"] >= 3
    assert _qualification_detail(result).startswith("selected now: gpt-oss-120b")


class _Browser:
    """No live portal: the listing route never becomes usable."""

    def __init__(self, page, *, on_route: bool):
        self.page, self.on_route, self.calls = page, on_route, 0

    async def goto_base_and_complete_sso(self, url):
        self.calls += 1
        raise RuntimeError("HIP_ROUTE_NOT_COMMITTED: ReAct navigation controller could not reach doctypes; "
                           "final observation={'target_match': True, 'target_usable': False, 'logged_in': True}")

    async def wait_ready(self):
        return None

    def _same_target_path(self, a, b):
        return self.on_route


def _cfg(tmp: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp / "memory")
    cfg.reporting.runs_dir = str(tmp / "runs")
    return cfg


def test_on_the_right_route_the_models_are_qualified_from_what_is_rendered(tmp_path: Path, monkeypatch):
    import hip_id_agent.model_portfolio as mp
    from hip_id_agent.live_runtime_certification import qualify_models_on_live_page

    monkeypatch.delenv("HIP_MODEL_ROUTER_SELECTED_TEXT", raising=False)
    screen = json.loads((Path(__file__).parent / "fixtures" / "doctypes_listing_screen.json").read_text())
    router = _router(tmp_path, _Client({"gpt-oss-120b": 1.0}), ["gpt-oss-120b"])
    monkeypatch.setattr(mp, "model_portfolio_from_config", lambda config: router)
    _grade_from_page(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg.aia.enabled = True

    class _Page:
        url = DOCTYPES

        async def evaluate(self, script):
            return screen

        async def wait_for_timeout(self, ms):
            return None

    browser = _Browser(_Page(), on_route=True)
    result = asyncio.run(qualify_models_on_live_page(cfg, browser, tmp_path / "cert"))
    assert browser.calls == 2  # opened once more
    assert result["status"] == "selected" and result["navigation"]["on_target_route"] is True


def test_a_rerun_that_cannot_finish_keeps_the_model_selected_before(tmp_path: Path):
    from hip_id_agent.live_runtime_certification import _qualification_detail, qualify_models_on_live_page
    from hip_id_agent.model_qualification import SCHEMA, load_selection, selection_path
    from hip_id_agent.safe_io import safe_write_json

    cfg = _cfg(tmp_path)
    earlier = {"schema_version": SCHEMA, "status": "selected", "locked": True, "selected_model": "mistral-small-3-1-24b-instruct-2503",
               "correct": 8, "total": 8, "accuracy": 1.0, "qualified_at": "2026-09-27T10:00:00Z", "fallback_order": []}
    safe_write_json(selection_path(cfg), earlier)
    result = asyncio.run(qualify_models_on_live_page(cfg, _Browser(None, on_route=False), tmp_path / "cert", force=True))
    assert result["status"] == "kept_previous_selection" and result["locked"] is True
    assert result["selected_model"] == "mistral-small-3-1-24b-instruct-2503" and "HIP_ROUTE_NOT_COMMITTED" in result["requalify_error"]
    assert load_selection(cfg)["selected_model"] == "mistral-small-3-1-24b-instruct-2503"  # the lock is untouched
    assert _qualification_detail(result).startswith("kept: mistral-small-3-1-24b-instruct-2503 (8/8")


# --------------------------------------------------------------- 3. MLflow
def test_mlflow_records_runs_in_the_local_store_again(tmp_path: Path, monkeypatch):
    pytest.importorskip("mlflow")
    from hip_id_agent.mlflow_async import AsyncMLflowTracker

    monkeypatch.delenv("MLFLOW_ALLOW_FILE_STORE", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    tracker = AsyncMLflowTracker(AppConfig().mlflow, run_id="RUN-1", run_dir=tmp_path / "runs" / "RUN-1", phases=[PHASE])
    status = tracker.start()
    assert status["available"] is True and status["error_count"] == 0, status["last_error"]
    tracker.finish(status="complete", application_complete=True)
    assert (tmp_path / "runs" / "mlruns").is_dir()


def test_one_mission_does_not_redirect_the_next_ones_mlflow_store(tmp_path: Path, monkeypatch):
    pytest.importorskip("mlflow")
    import os

    from hip_id_agent.mlflow_async import AsyncMLflowTracker

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    first = AsyncMLflowTracker(AppConfig().mlflow, run_id="A", run_dir=tmp_path / "one" / "A", phases=[PHASE])
    first.start()
    first.finish(status="complete", application_complete=True)
    assert "MLFLOW_TRACKING_URI" not in os.environ  # mlflow.set_tracking_uri exported it before
    second = AsyncMLflowTracker(AppConfig().mlflow, run_id="B", run_dir=tmp_path / "two" / "B", phases=[PHASE])
    assert second.tracking_uri_source == "local_file_fallback" and "/two/mlruns" in second.tracking_uri


# ------------------------------------------------------- 4. run-history learning
def _events(rows: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps({"event": e, "payload": p}) for e, p in rows) + "\n"


def _run(root: Path, name: str, *, doc_status: str, attempts: List[Dict[str, Any]], durations: List[float],
         blocked_reason: str = "", render: List[Dict[str, Any]] = (), finished: bool = True) -> Path:
    run = root / name
    (run / PHASE).mkdir(parents=True)
    (run / "mcp_runtime").mkdir()
    row = {"status": doc_status, "attempts": len(attempts) or 1, "started_at": "2026-09-28T10:00:00+00:00"}
    if doc_status == "complete":
        row["completed_at"] = "2026-09-28T10:%02d:00+00:00" % min(59, int(sum(durations) / 60))
    if blocked_reason:
        row["blocked_reason"] = blocked_reason
    (run / "mission_state.json").write_text(json.dumps({"mission_status": doc_status if finished else "in_progress",
                                                        "phases": {PHASE: row}}))
    events = [("phase_completed", {"phase": PHASE, "attempt": i + 1, "judge_pass": doc_status == "complete" and i == len(durations) - 1,
                                   "duration_seconds": d}) for i, d in enumerate(durations)]
    if finished:
        events.append(("mission_finished", {"status": doc_status, "duration_seconds": sum(durations)}))
    (run / "mlflow_async_events.jsonl").write_text(_events(events))
    (run / PHASE / "phase_execution_attempts.json").write_text(json.dumps(attempts))
    (run / "mcp_runtime" / "navigation_render_waits.jsonl").write_text("".join(json.dumps(r) + "\n" for r in render))
    return run


def _history(root: Path) -> None:
    # Like run UHAUL-POASN-20260928-130654: stopped by the watchdog, then blocked.
    _run(root, "UHAUL-A", doc_status="blocked", durations=[],
         attempts=[{"attempt": 1, "status": "failed", "error": "HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field"},
                   {"attempt": 2, "status": "failed", "error": "HIP_PHASE_STALL_AFTER_RECOVERY"}],
         blocked_reason="HIP_PHASE_STALL_AFTER_RECOVERY after HIP_PHASE_NO_PROGRESS_WATCHDOG",
         render=[{"module_key": "doctypes", "seconds": 70.0, "usable": True},
                 {"module_key": "doctypes", "seconds": 92.0, "usable": False}])
    # A run where the watchdog fired, and the next attempt completed after 25 minutes.
    _run(root, "UHAUL-B", doc_status="complete", durations=[600.0, 1500.0],
         attempts=[{"attempt": 1, "status": "failed", "error": "HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field"}],
         render=[{"module_key": "doctypes", "seconds": 48.0, "usable": True}])


def test_past_run_folders_are_learned_once_and_teach_the_time_they_need(tmp_path: Path):
    from hip_id_agent.run_history_learning import run_history_learner_from_config

    cfg = _cfg(tmp_path)
    _history(Path(cfg.reporting.runs_dir))
    learner = run_history_learner_from_config(cfg)
    lessons = learner.learn(include_mlflow=False)
    assert lessons["runs_learned"] == 2 and lessons["new_run_count"] == 2
    learned = lessons["lessons"]
    # A successful Document Type attempt took 1500 s: the next attempts get p90 x 1.25.
    assert learned["min_attempt_seconds"][PHASE] == 1875
    # The watchdog stopped an attempt of a phase that then completed: the portal was slow.
    assert learned["no_progress_watchdog_seconds"][PHASE] == 135
    # The listing rendered in up to 70 s and once not in time: wait longer for it.
    assert learned["navigation_render_wait_seconds"]["doctypes"] == 135
    codes = dict(lessons["phases"][PHASE]["top_failure_codes"])
    assert codes["HIP_PHASE_NO_PROGRESS_WATCHDOG"] == 2 and codes["HIP_PHASE_STALL_AFTER_RECOVERY"] == 1
    # A stop recorded by several files (attempts, mission state) counts once per run.
    facts = json.loads(learner.facts_path.read_text())["runs"]
    assert facts["UHAUL-A"]["phases"][PHASE]["failure_codes"]["HIP_PHASE_NO_PROGRESS_WATCHDOG"] == 1
    assert all(r.get("why") for r in lessons["reasons"])
    # Each run is read once.
    again = learner.learn(include_mlflow=False)
    assert again["new_run_count"] == 0 and again["runs_learned"] == 2


def test_a_run_still_running_is_read_again_when_it_has_finished(tmp_path: Path):
    from hip_id_agent.run_history_learning import run_history_learner_from_config

    cfg = _cfg(tmp_path)
    root = Path(cfg.reporting.runs_dir)
    run = _run(root, "RUNNING", doc_status="in_progress", durations=[], attempts=[], finished=False)
    learner = run_history_learner_from_config(cfg)
    assert learner.learn(include_mlflow=False)["phases"][PHASE]["completed"] == 0
    (run / "mission_state.json").write_text(json.dumps({"mission_status": "complete", "phases": {PHASE: {
        "status": "complete", "attempts": 1, "started_at": "2026-09-28T10:00:00+00:00", "completed_at": "2026-09-28T10:20:00+00:00"}}}))
    later = learner.learn(include_mlflow=False)
    assert later["refreshed_runs"] == ["RUNNING"] and later["phases"][PHASE]["completed"] == 1


def test_lessons_never_shorten_a_budget(tmp_path: Path):
    from hip_id_agent.run_history_learning import lesson_value, run_history_learner_from_config

    cfg = _cfg(tmp_path)
    _run(Path(cfg.reporting.runs_dir), "FAST", doc_status="complete", durations=[120.0], attempts=[],
         render=[{"module_key": "doctypes", "seconds": 4.0, "usable": True}])
    lessons = run_history_learner_from_config(cfg).learn(include_mlflow=False)
    assert lessons["lessons"]["min_attempt_seconds"] == {} and lessons["lessons"]["navigation_render_wait_seconds"] == {}
    assert lesson_value(lessons, "min_attempt_seconds", PHASE, 900.0) == 900.0


def test_mlflow_runs_without_a_run_folder_are_learned_too(tmp_path: Path, monkeypatch):
    pytest.importorskip("mlflow")
    from hip_id_agent.mlflow_async import AsyncMLflowTracker
    from hip_id_agent.run_history_learning import run_history_learner_from_config

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    cfg = _cfg(tmp_path)
    tracker = AsyncMLflowTracker(cfg.mlflow, run_id="OTHER-MACHINE", run_dir=Path(cfg.reporting.runs_dir) / "OTHER-MACHINE", phases=[PHASE])
    tracker.start()
    tracker.phase_started(PHASE, attempt=1)
    tracker.log_metrics({f"phase/{PHASE}/failure/HIP_PHASE_NO_PROGRESS_WATCHDOG": 1.0}, step=1)
    tracker.phase_blocked(PHASE, attempt=1, reason="HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field")
    tracker.phase_started(PHASE, attempt=2)
    tracker._phase_start[PHASE] = time.time() - 1600  # this attempt took ~1600 s
    tracker.phase_completed(PHASE, attempt=2, judge_pass=True)
    tracker.log_metrics({"navigation/doctypes/render_seconds": 80.0})
    tracker.finish(status="complete", application_complete=True)
    import shutil

    shutil.rmtree(Path(cfg.reporting.runs_dir) / "OTHER-MACHINE")  # only MLflow knows this run
    lessons = run_history_learner_from_config(cfg).learn()
    assert lessons["mlflow"]["status"] == "ok" and lessons["runs_from_mlflow"] == 1
    assert lessons["lessons"]["min_attempt_seconds"][PHASE] >= 1990  # 1600 s x 1.25
    assert lessons["lessons"]["no_progress_watchdog_seconds"][PHASE] == 135
    assert lessons["lessons"]["navigation_render_wait_seconds"]["doctypes"] == 120
    assert run_history_learner_from_config(cfg).learn()["new_run_count"] == 0  # once


def test_the_next_mission_uses_the_lessons(tmp_path: Path):
    from hip_id_agent.browser_session import BrowserSession
    from hip_id_agent.run_history_learning import apply_lessons, lesson_value, run_history_learner_from_config
    from hip_id_agent.runtime_self_heal import RuntimeSelfHealController

    cfg = _cfg(tmp_path)
    _history(Path(cfg.reporting.runs_dir))
    _run(Path(cfg.reporting.runs_dir), "SLOW-PHASE", doc_status="complete", durations=[5000.0], attempts=[])
    lessons = run_history_learner_from_config(cfg).learn(include_mlflow=False)
    browser = BrowserSession(cfg, tmp_path / "mission")
    healer = RuntimeSelfHealController(config=cfg, root_dir=tmp_path / "mission", browser=browser)
    assert healer.wall_budget_seconds(PHASE) == 3600
    applied = apply_lessons(lessons, healer=healer, browser=browser)
    assert healer.wall_budget_seconds(PHASE) == applied["phase_wall_seconds"][PHASE] > 3600
    assert healer.wall_budget_seconds("data_map") == 3600  # other phases unchanged
    assert browser._navigation_render_budget(DOCTYPES) == 135
    assert browser._navigation_render_budget(DOCTYPES.replace("doctypes", "datamaps")) == 90
    assert lesson_value(lessons, "no_progress_watchdog_seconds", PHASE, 90.0) == 135


def test_the_mission_learns_before_its_first_phase_and_records_what_later_runs_learn_from():
    from hip_id_agent.dummy_fill_e2e import FullDummyFillE2EFlow

    source = inspect.getsource(FullDummyFillE2EFlow.run)
    learn_at = source.index("run_history_learner_from_config(self.config).learn")
    assert learn_at < source.index("while runtime_self_healer.until_complete or attempt_index < max_phase_attempts")
    assert "apply_lessons(run_history_lessons, healer=runtime_self_healer, browser=shared_browser)" in source
    budget_at = source.index("remaining_phase_seconds = max(")
    assert 'lesson_value(run_history_lessons, "min_attempt_seconds", phase' in source[budget_at:budget_at + 400]
    assert 'run_history_lessons, "no_progress_watchdog_seconds", phase' in source
    assert 'f"phase/{phase}/failure/{code}"' in source and 'f"navigation/{k}/render_seconds"' in source
    assert '"run_history_learning.json"' in source


def test_the_control_center_shows_what_was_learned(tmp_path: Path):
    import backend.app as appmod
    from hip_id_agent.run_history_learning import run_history_learner_from_config

    cfg = _cfg(tmp_path)
    _history(Path(cfg.reporting.runs_dir))
    run_history_learner_from_config(cfg).learn(include_mlflow=False)
    summary = appmod._run_history_summary(cfg)
    assert summary["status"] == "learned" and summary["runs_learned"] == 2 and summary["lesson_count"] >= 3
    routes = {getattr(r, "path", "") for r in appmod.app.routes}
    assert {"/api/learning/run-history", "/api/learning/run-history/refresh"} <= routes
    root = Path(__file__).resolve().parents[1]
    for base in ("webui", "backend/webui"):
        html = (root / base / "index.html").read_text(encoding="utf-8")
        js = (root / base / "app.js").read_text(encoding="utf-8")
        assert 'id="runHistoryMetric"' in html and 'id="runHistoryLoad"' in html
        assert "/api/learning/run-history" in js and "run_history_learning" in js


def test_defaults():
    cfg = AppConfig()
    assert cfg.portal.navigation_render_wait_seconds == 90 and cfg.portal.navigation_render_wait_max_seconds == 300
    rh = cfg.run_history_learning
    assert rh.enabled and rh.apply_at_mission_start and rh.include_mlflow and rh.max_budget_multiplier == 3.0
