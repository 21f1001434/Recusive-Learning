from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.config import AppConfig
from hip_id_agent.dummy_fill_e2e import _artifact_actual_state
from hip_id_agent.live_readiness import _browser_launch_plan
from hip_id_agent.runtime_env import configure_utf8_stdio
from hip_id_agent.section_judge import DualModelSectionJudge, build_phase_expectation


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_chrome_is_primary_default_and_edge_is_startup_fallback():
    cfg = AppConfig()
    assert cfg.portal.chromium_channel == "chrome"
    assert cfg.portal.browser_user_data_dir.endswith("chrome_profile")
    assert cfg.portal.fallback_to_edge is True
    assert cfg.portal.edge_user_data_dir.endswith("edge_profile")


def test_all_shipped_configs_are_chrome_first():
    for name in ("config.yaml", "config.example.yaml", "config.mcp-required.windows.yaml"):
        raw = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        portal = raw["portal"]
        assert portal["chromium_channel"] == "chrome"
        assert portal["browser_user_data_dir"].endswith("chrome_profile")
        assert portal["fallback_to_edge"] is True
        assert portal["edge_user_data_dir"].endswith("edge_profile")


def test_browser_session_and_readiness_share_exact_chrome_first_order(tmp_path: Path):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.edge_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    session = BrowserSession(cfg, tmp_path / "run")
    session_order = [row["name"] for row in session._managed_browser_candidates()]
    readiness_order = [name for name, _kwargs, enabled in _browser_launch_plan(cfg.portal) if enabled]
    assert session_order == readiness_order == ["chrome", "edge", "playwright_chromium"]


def test_explicit_edge_override_remains_supported(tmp_path: Path):
    cfg = AppConfig()
    cfg.portal.chromium_channel = "msedge"
    cfg.portal.browser_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.edge_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    session = BrowserSession(cfg, tmp_path / "run")
    assert [row["name"] for row in session._managed_browser_candidates()] == ["edge", "chrome", "playwright_chromium"]


def test_configure_utf8_stdio_overrides_bad_parent_encoding(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONLEGACYWINDOWSSTDIO", "1")
    result = configure_utf8_stdio()
    assert result["PYTHONUTF8"] == "1"
    assert result["PYTHONIOENCODING"] == "utf-8"
    assert result["PYTHONLEGACYWINDOWSSTDIO"] == "0"


def test_utf8_configuration_handles_nonbreaking_hyphen_without_encode_failure():
    configure_utf8_stdio()
    text = "Route handoff — source\u2011transport\u2011profile"
    assert text.encode("utf-8").decode("utf-8") == text


def test_artifact_actual_state_keeps_normal_writable_nonrequired_control(tmp_path: Path):
    state = _artifact_actual_state(tmp_path, [], [{
        "selector": "input[name=ruleName]",
        "label": "Rule Name",
        "value": "DELLCoXMLASNXX08C_U-HAUL_RULE",
        "required": False,
        "disabled": False,
        "readonly": False,
        "section": "Rule Details",
    }])
    assert len(state["controls"]) == 1
    assert state["controls"][0]["value"] == "DELLCoXMLASNXX08C_U-HAUL_RULE"
    assert state["controls"][0]["evidence"] == "captured_form_inventory"


def test_artifact_actual_state_drops_empty_inventory_control(tmp_path: Path):
    state = _artifact_actual_state(tmp_path, [], [{"label": "Rule Name", "value": "", "required": True}])
    assert state["controls"] == []


@pytest.mark.parametrize("phase", [
    "data_map", "source_document_type", "target_document_type", "rule",
    "source_transport_profile", "target_transport_profile", "biz_flow",
])
def test_all_mission_phases_compile_field_bound_semantic_judge_contract(phase: str):
    expected = build_phase_expectation(EXAMPLE, phase)
    assert expected["expectation_source"] == "stateful_phase_graph"
    assert expected["stateful"] is True
    assert expected["facts"]
    assert all(str(f.get("field") or "").strip() for f in expected["facts"])
    assert all(f.get("value") not in (None, "", []) for f in expected["facts"])
    # The old bug emitted anonymous input_value[N] facts with no field ownership.
    assert not any(str(f.get("field") or "").startswith("input_value[") for f in expected["facts"])


def test_rule_repeatable_condition_contract_preserves_row_identity():
    expected = build_phase_expectation(EXAMPLE, "rule")
    rows = [f for f in expected["facts"] if f.get("row_kind") == "condition"]
    assert rows
    assert {f.get("row_index") for f in rows} >= {0, 1}
    assert any(f["field"] == "condition_type" and f["row_index"] == 0 for f in rows)


def test_bizflow_repeatable_contract_preserves_nested_row_identity():
    expected = build_phase_expectation(EXAMPLE, "biz_flow")
    kinds = {str(f.get("row_kind") or "") for f in expected["facts"]}
    assert "flow_identifier" in kinds
    assert "process_step" in kinds
    assert "routing_condition" in kinds


def test_deterministic_judge_matches_writable_control_by_field_alias_not_global_text():
    judge = DualModelSectionJudge()
    expected = {
        "facts": [{
            "field": "rule_name", "value": "EXPECTED-RULE", "aliases": ["Rule Name"],
            "required": True, "section": "Rule Details", "section_aliases": [],
            "row_kind": "", "row_index": None, "match_mode": "exact",
        }],
        "row_counts": {},
    }
    actual = {
        "controls": [
            {"label": "Description", "value": "EXPECTED-RULE", "section": "Rule Details", "type": "text"},
            {"label": "Rule Name", "value": "EXPECTED-RULE", "section": "Rule Details", "type": "text"},
        ],
        "visible_text": "EXPECTED-RULE",
        "row_counts": {},
    }
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is True
    assert result["matched_values"][0]["field"] == "rule_name"
    assert result["matched_values"][0]["actual"] == "EXPECTED-RULE"


def test_deterministic_judge_rejects_same_value_in_adjacent_wrong_field():
    judge = DualModelSectionJudge()
    expected = {
        "facts": [{
            "field": "rule_name", "value": "EXPECTED-RULE", "aliases": ["Rule Name"],
            "required": True, "section": "Rule Details", "section_aliases": [],
            "row_kind": "", "row_index": None, "match_mode": "exact",
        }],
        "row_counts": {},
    }
    actual = {
        "controls": [{"label": "Description", "value": "EXPECTED-RULE", "section": "Rule Details", "type": "text"}],
        "visible_text": "Rule Name EXPECTED-RULE",
        "row_counts": {},
    }
    result = judge.deterministic_judge(expected=expected, actual_state=actual, attempts=[])
    assert result["pass"] is False
    assert result["missing_values"][0]["field"] == "rule_name"


@pytest.mark.asyncio
async def test_action_trace_uses_destination_phase_override_during_handoff(tmp_path: Path):
    cfg = AppConfig()
    cfg.browser_use.capture_state_after_actions = False
    session = BrowserSession(cfg, tmp_path / "run")
    seen = []

    class Trace:
        def record_action(self, event, *, phase=""):
            seen.append(phase)

    session.mission_trace = Trace()
    session._active_phase_name = "source_document_type"
    session._trace_phase_override = "target_document_type"
    event = await session._begin_action("navigate", "Transport Profiles")
    await session._finish_action(event, True)
    assert seen == ["target_document_type"]


@pytest.mark.asyncio
async def test_handoff_temporarily_attributes_navigation_to_destination_phase(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.browser_use.capture_state_after_actions = False
    session = BrowserSession(cfg, tmp_path / "run")
    session._active_phase_name = "source_document_type"
    seen = []

    class Trace:
        def record_transition(self, *args, **kwargs):
            pass
        def record_observation(self, phase, **kwargs):
            seen.append(("observation", phase))

    session.mission_trace = Trace()
    session.page = SimpleNamespace(url="https://developer.dell.com/hybrid-integrations/bizlink/document-types")

    async def cleanup(**kwargs):
        assert session._trace_phase_override == ""
        return {"status": "ok"}

    async def navigate(_url):
        seen.append(("navigate_phase", session._trace_phase_override))
        session.page.url = _url

    async def usable(_url):
        return True

    async def same_surface(*args, **kwargs):
        return {"pass": True}

    monkeypatch.setattr(session, "_dismiss_transient_ui", cleanup)
    monkeypatch.setattr(session, "navigate", navigate)
    monkeypatch.setattr(session, "_navigation_page_is_usable", usable)
    monkeypatch.setattr(session, "_verify_dual_mcp_same_surface", same_surface)

    result = await session.handoff_to_next_phase(
        from_phase="source_document_type",
        to_phase="target_document_type",
        to_url="https://developer.dell.com/hybrid-integrations/bizlink/document-types",
        exact_checkpoint_passed=True,
    )
    assert result["pass"] is True
    assert ("navigate_phase", "target_document_type") in seen
    assert ("observation", "target_document_type") in seen
    assert session._trace_phase_override == ""


@pytest.mark.asyncio
async def test_default_chrome_launch_failure_falls_back_to_edge(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.edge_user_data_dir = str(tmp_path / "edge")
    cfg.portal.chromium_user_data_dir = str(tmp_path / "chromium")
    cfg.portal.fallback_to_playwright_chromium = False
    session = BrowserSession(cfg, tmp_path / "run")

    class Chromium:
        async def launch_persistent_context(self, **kwargs):
            channel = kwargs.get("channel") or "chromium"
            if channel == "chrome":
                raise RuntimeError("Chrome blocked by workstation policy")
            return SimpleNamespace(name=channel, close=lambda: None)

    session._playwright = SimpleNamespace(chromium=Chromium())
    monkeypatch.setattr(session, "_wait_for_cdp_ready", lambda timeout_seconds=8.0: __import__("asyncio").sleep(0, result={"ok": True}))
    ctx = await session._launch_managed_context_with_fallback(common_kwargs={"args": []}, needs_local_cdp=True)
    assert ctx.name == "msedge"
    assert session._selected_browser["browser"] == "edge"
    assert session._selected_browser["fallback_used"] is True
    assert [row["browser"] for row in session._browser_launch_attempts] == ["chrome", "edge"]


@pytest.mark.asyncio
async def test_unhealthy_chrome_cdp_is_closed_then_edge_is_selected(tmp_path: Path, monkeypatch):
    cfg = AppConfig()
    cfg.portal.browser_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.chrome_user_data_dir = str(tmp_path / "chrome")
    cfg.portal.edge_user_data_dir = str(tmp_path / "edge")
    cfg.portal.fallback_to_playwright_chromium = False
    session = BrowserSession(cfg, tmp_path / "run")

    class Context:
        def __init__(self, name):
            self.name = name
            self.closed = False
        async def close(self):
            self.closed = True

    class Chromium:
        def __init__(self):
            self.contexts = []
        async def launch_persistent_context(self, **kwargs):
            ctx = Context(kwargs.get("channel") or "chromium")
            self.contexts.append(ctx)
            return ctx

    chromium = Chromium()
    session._playwright = SimpleNamespace(chromium=chromium)
    health = iter([{"ok": False, "error": "Chrome CDP unavailable"}, {"ok": True, "browser": "Edge"}])

    async def probe(timeout_seconds=8.0):
        return next(health)

    monkeypatch.setattr(session, "_wait_for_cdp_ready", probe)
    ctx = await session._launch_managed_context_with_fallback(common_kwargs={"args": []}, needs_local_cdp=True)
    assert chromium.contexts[0].name == "chrome"
    assert chromium.contexts[0].closed is True
    assert ctx.name == "msedge"
    assert session._selected_browser["browser"] == "edge"


def test_mission_trace_judge_summary_preserves_concise_missing_field_diagnostics(tmp_path: Path):
    from hip_id_agent.mission_trace import MissionTraceLedger

    root = tmp_path / "mission"
    phase_dir = root / "rule"
    phase_dir.mkdir(parents=True)
    ledger = MissionTraceLedger(root, run_id="R1", phases=["rule"])
    ledger.refresh_phase_artifacts(
        "rule",
        phase_dir,
        verification={"status": "failed"},
        judge={
            "pass": False,
            "status": "blocked",
            "deterministic_judge": {
                "pass": False,
                "missing_values": [{"field": "rule_name", "expected": "RULE-1", "reason": "exact committed value was not found"}],
                "row_issues": [],
                "failed_attempts": [],
            },
            "text_model_judge": {"status": "ok", "pass": False},
            "vision_model_judge": {"status": "ok", "pass": True},
        },
    )
    step = ledger.state["steps"][0]
    assert step["judge"]["pass"] is False
    assert step["judge"]["missing_fields"][0]["field"] == "rule_name"
    assert step["judge"]["text_model_status"] == "ok"
    assert step["judge"]["vision_model_status"] == "ok"


def test_release_version_is_223():
    import hip_id_agent
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
