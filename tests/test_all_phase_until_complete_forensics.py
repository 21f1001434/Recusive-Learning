from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.deterministic_evidence import build_validated_phase_trajectory
from hip_id_agent.flow_pattern_memory import FlowPatternMemory
from hip_id_agent.runtime_self_heal import RuntimeSelfHealController


class _Body:
    async def inner_text(self, timeout=0):
        return "Create Data Map Document Type Rule Transport Profile BizFlow"


class _Page:
    url = "https://developer.dell.com/hybrid-integrations/securelink/rules"

    def locator(self, selector):
        assert selector == "body"
        return _Body()

    async def evaluate(self, script):
        return {
            "url": self.url,
            "title": "HIP",
            "readyState": "complete",
            "activeElement": {"tag": "input", "label": "Name"},
            "forms": [{"classes": "ng-valid", "nativeValid": True, "invalidLabels": []}],
            "controls": [{"label": "Name", "formControlName": "ruleName", "selectorHint": '[formcontrolname="ruleName"]'}],
            "popups": [],
            "overlays": [],
            "headings": ["Create Rule"],
            "angular": {"ngPending": 0, "ngInvalid": 0, "ngValid": 1},
        }


class _Backend:
    async def get_dom_snapshot(self):
        return {"snapshot_text": "Create Rule Name Conditions Actions"}

    async def get_network_events(self):
        return [{"method": "GET", "url": "https://developer.dell.com/api/rules"}]

    async def get_console_messages(self):
        return [{"type": "log", "text": "ready"}]


class _Browser:
    def __init__(self):
        self.page = _Page()
        self.playwright_mcp_backend = _Backend()
        self.mcp_backend = _Backend()
        self.console_messages = [{"text": "local console"}]
        self.network_tab_events = [{"url": "https://developer.dell.com/api/rules"}]
        self.action_events = [{"type": "fill", "target": "Name"}]
        self.dom_transition_records = [{"before": "empty", "after": "filled"}]
        self._phase_history = [{"phase": "rule"}]

    async def _observe_react_navigation_state(self, target_url):
        return {"target_match": True, "target_usable": True, "same_actual_surface": True, "logged_in": True}

    async def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"png")
        return str(path)

    async def save_dom_snapshot(self, name):
        return {"html": f"{name}.html", "text": f"{name}.txt"}

    async def _ensure_active_page(self, target_url=""):
        return self.page

    async def _dismiss_transient_ui(self, next_phase=""):
        return {"pass": True}

    async def _consolidate_session_pages(self, target_url=""):
        return {"pass": True}

    async def _ensure_page_observers(self):
        return None

    async def goto_base_and_complete_sso(self, target_url):
        self.page.url = target_url


@pytest.mark.asyncio
async def test_failure_forensics_capture_all_three_observation_channels(tmp_path: Path):
    cfg = AppConfig()
    cfg.aia.enabled = False
    cfg.runtime_self_heal.forensic_evidence = True
    controller = RuntimeSelfHealController(
        config=cfg,
        root_dir=tmp_path,
        browser=_Browser(),
        forensic_evidence=True,
    )
    decision = await controller.handle_failure(
        phase="rule",
        target_url=_Page.url,
        attempt=1,
        message="required semantic control not found for Mapping Identifier Name (Version)",
    )
    evidence_dir = Path(decision.evidence_dir)
    manifest = json.loads((evidence_dir / "forensic_evidence_manifest.json").read_text(encoding="utf-8"))
    assert manifest["channels"]["local_playwright"]["available"] is True
    assert manifest["channels"]["playwright_mcp"]["dom"]["available"] is True
    assert manifest["channels"]["playwright_mcp"]["network"]["available"] is True
    assert manifest["channels"]["chrome_devtools_mcp"]["console"]["available"] is True
    assert (evidence_dir / "browser_event_history.json").exists()


def _graph():
    return {
        "phase": "rule",
        "object_family": "rule",
        "nodes": [{
            "node_id": "rule.details.name",
            "section": "Rule",
            "row_kind": "",
            "row_index": None,
            "field_key": "rule_name",
            "action": "fill_text",
            "expected_value": "DO NOT STORE THIS VALUE",
            "required": True,
            "depends_on": [],
        }],
    }


def _execution():
    return {
        "schema_version": "hip.stateful-form-execution.v2",
        "pass": True,
        "attempts": [{
            "node_id": "rule.details.name",
            "field": "rule_name",
            "section": "Rule",
            "row_kind": "",
            "row_index": None,
            "label": "Name",
            "occurrence": 0,
            "selector": "input#dds-form-field-123456",
            "executor": "native_text_control",
            "dom_events": ["input", "change", "blur"],
            "control_kind": {"tag": "input", "role": "", "type": "text"},
            "success": True,
            "binding_diagnostics": {
                "selected_identity": "rule||0|name|rulename|input|text|name",
                "control": {"framework_key": "ruleName", "label": "Name", "tag": "input", "role": ""},
            },
            "transaction_proof": {"protected_state_changes": []},
        }],
        "final_form_state_model": {"one_to_one_pass": True},
    }


def test_flow_memory_promotes_stable_selectors_and_interaction_profiles(tmp_path: Path):
    memory = FlowPatternMemory(tmp_path / "flow_patterns", minimum_similarity=0.7)
    promoted = memory.promote(graph=_graph(), execution=_execution(), phase="rule", run_id="run-1", judge_pass=True)
    assert promoted["selector_profile_count"] == 1
    assert promoted["interaction_profile_count"] == 1
    applied = memory.apply_to_graph(_graph(), phase="rule")
    node = applied["nodes"][0]
    profile = node["validated_memory_selector_profile"]
    assert profile["candidates"][0]["selector"] == '[formcontrolname="ruleName"]'
    assert node["validated_memory_interaction_profile"]["executor"] == "native_text_control"
    raw = (tmp_path / "flow_patterns" / "patterns.json").read_text(encoding="utf-8")
    assert "DO NOT STORE THIS VALUE" not in raw


def test_success_trajectory_is_value_free_and_contains_replay_evidence(tmp_path: Path):
    phase_dir = tmp_path / "rule"
    artifact = phase_dir / "rule_kb" / "rule_target_branch_execution.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_execution()), encoding="utf-8")
    result = build_validated_phase_trajectory(
        phase="rule",
        phase_dir=phase_dir,
        attempt=2,
        verification={"status": "passed"},
        judge_result={"pass": True},
        golden_references=[{"path": str(tmp_path / "Rules.png")}],
        portal_learning_summary={"page_model": {"fingerprint": "abc", "url_template": "https://developer.dell.com/.../rules"}},
    )
    assert result["trust"] == "validated"
    assert result["ordered_steps"][0]["selector_profile"]["candidates"][0]["strategy"] == "framework_key"
    assert result["replay_policy"]["exploit_validated_path_first"] is True
    assert result["values_stored"] is False
    assert Path(result["path"]).exists()


def test_all_phases_until_complete_cli_profile_is_wired():
    root = Path(__file__).resolve().parents[1]
    source = (root / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    flow = (root / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert '"--all-phases-until-complete"' in source
    assert 'selected_phases = list(PHASE_SEQUENCE)' in source
    assert 'runtime_self_heal_until_complete = True' in source
    assert 'forensic_evidence=forensic_evidence' in source
    assert 'build_validated_phase_trajectory' in flow
