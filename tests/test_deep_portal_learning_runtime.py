from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.models import ActionEvent, NetworkTabEvent
from hip_id_agent.portal_brain import PortalBrain
from hip_id_agent.portal_learning_runtime import PortalLearningRuntime, _endpoint_template


class _Page:
    def __init__(self):
        self.calls = 0

    async def evaluate(self, script):
        self.calls += 1
        controls = [
            {
                "index": 0,
                "tag": "input",
                "role": "combobox",
                "type": "text",
                "name": "usage",
                "id": "dds-form-field-92831",
                "label": "Usage",
                "ariaLabel": "Usage",
                "placeholder": "Select",
                "required": True,
                "disabled": False,
                "invalid": False,
                "multiple": True,
                "hasValue": self.calls > 1,
                "selectedLabels": ["Routing", "Mapping"] if self.calls > 1 else [],
                "validation": {"min": None, "max": None, "minLength": None, "maxLength": "50", "pattern": None},
                "box": {"x": 10, "y": 20, "width": 300, "height": 32},
            }
        ]
        if self.calls > 1:
            controls.append({
                "index": 1,
                "tag": "button",
                "role": "button",
                "type": "button",
                "name": "",
                "id": "",
                "label": "Add Attribute",
                "ariaLabel": "",
                "placeholder": "",
                "required": False,
                "disabled": False,
                "invalid": False,
                "multiple": False,
                "hasValue": False,
                "selectedLabels": [],
                "validation": {},
                "box": {"x": 10, "y": 80, "width": 120, "height": 32},
            })
        return {
            "url": "https://developer.dell.com/hybrid-integrations/securelink/doctypes",
            "title": "Document Types",
            "readyState": "complete",
            "headings": ["Create Document Type", "Attributes to Configure"],
            "tabs": [{"text": "Document Details", "selected": True}],
            "surfaces": [{"tag": "form", "role": "", "text": "Create Document Type"}],
            "controls": controls,
            "alerts": [],
            "storageKeyNames": {"localStorage": ["hip.preferences"], "sessionStorage": ["route.cache"]},
            "storageValuesCaptured": False,
            "bodyFingerprintSource": "Create Document Type Document Identifier Attributes to Configure",
        }


class _Client:
    def __init__(self, tools):
        self.tools = {name: object() for name in tools}


class _PW:
    def __init__(self):
        self.client = _Client(["browser_snapshot", "browser_evaluate", "browser_network_requests", "browser_console_messages"])

    async def snapshot(self, **kwargs):
        return {"text": "heading Create Document Type textbox Document Identifier combobox Usage"}


class _CDP:
    def __init__(self):
        self.client = _Client(["take_snapshot", "list_network_requests", "list_console_messages", "evaluate_script", "performance_start_trace", "performance_stop_trace"])
        self.started = False

    async def get_dom_snapshot(self):
        return {"snapshot_text": "Create Document Type Document Identifier Attributes to Configure"}

    async def start_performance_trace(self, **kwargs):
        self.started = True
        return {"started": True}

    async def stop_performance_trace(self):
        return {"captured": True, "insights": ["long task"]}


class _Browser:
    def __init__(self):
        self.page = _Page()
        self.playwright_mcp_backend = _PW()
        self.mcp_backend = _CDP()
        self.action_events = []
        self.network_tab_events = []
        self.dom_transition_records = []
        self.console_messages = []


@pytest.mark.asyncio
async def test_deep_learning_builds_validated_portal_model_and_promotes_to_brain(tmp_path: Path):
    cfg = AppConfig()
    browser = _Browser()
    brain = PortalBrain(tmp_path / "brain")
    runtime = PortalLearningRuntime(config=cfg, root_dir=tmp_path / "run", browser=browser, brain=brain, run_id="run-1")
    contract = {
        "surface_markers": ["Document Type", "Document Identifier", "Attributes to Configure"],
        "exact_state_checks": ["identifier", "usage exact set"],
        "repeatable_kinds": ["attribute"],
    }
    await runtime.begin_attempt(phase="source_document_type", attempt=2, phase_dir=tmp_path / "run" / "source_document_type", contract=contract)
    browser.action_events.append(ActionEvent(
        action_id="a1", type="click", target="Usage", page_url_before="https://developer.dell.com/hybrid-integrations/securelink/doctypes",
        page_url_after="https://developer.dell.com/hybrid-integrations/securelink/doctypes", network_events_triggered=["r1"],
    ))
    browser.network_tab_events.append(NetworkTabEvent(
        request_id="r1", url="https://developer.dell.com/api/document-type/10483/details?environment=DEV", method="GET", status=200,
        response_body_redacted={"id": 10483, "attributes": [{"name": "Receiver"}]}, stage="doctype_details",
    ))
    browser.dom_transition_records.append({"parent": "Usage", "effect": "selected chips appeared"})
    result = await runtime.finish_attempt(
        phase="source_document_type", attempt=2, verification={"status": "pass"}, judge_result={"pass": True}, success=True,
    )
    assert result["trust"] == "validated"
    assert result["storage_values_captured"] is False
    assert result["storage_key_names"]["localStorage"] == ["hip.preferences"]
    assert result["api_contracts"][0]["endpoint_template"].endswith("/api/document-type/{id}/details")
    assert result["validation_rules"][0]["required"] is True
    assert result["performance"]["captured"] is True
    phase = brain._load_phase("source_document_type")
    assert phase["page_fingerprints"]
    assert phase["api_contracts"]
    assert phase["validation_rules"]
    assert phase["autonomous_learning_runs"][-1]["trust"] == "validated"


@pytest.mark.asyncio
async def test_failed_or_unjudged_learning_never_becomes_validated(tmp_path: Path):
    cfg = AppConfig()
    browser = _Browser()
    brain = PortalBrain(tmp_path / "brain")
    runtime = PortalLearningRuntime(config=cfg, root_dir=tmp_path / "run", browser=browser, brain=brain, run_id="run-2")
    await runtime.begin_attempt(phase="rule", attempt=1, phase_dir=tmp_path / "run" / "rule", contract={})
    result = await runtime.finish_attempt(
        phase="rule", attempt=1, verification={"status": "pass"}, judge_result={"pass": False}, success=True,
    )
    assert result["trust"] == "candidate"
    phase = brain._load_phase("rule")
    row = next(iter(phase["page_fingerprints"].values()))
    assert row["validated_count"] == 0
    assert row["candidate_count"] == 1


def test_endpoint_templates_remove_ids_but_keep_route_contract():
    value = _endpoint_template("https://developer.dell.com/api/document-type/10483/details?environment=DEV")
    assert value == "https://developer.dell.com/api/document-type/{id}/details"


def test_portal_learning_defaults_are_enabled_and_safe():
    cfg = AppConfig()
    assert cfg.portal_learning.enabled is True
    assert cfg.portal_learning.capture_performance_trace is True
    assert cfg.portal_learning.performance_trace_on_retry_only is True
    assert cfg.portal_learning.promote_only_after_judge_pass is True


def test_full_run_integrates_learning_for_every_phase_attempt():
    source = (Path(__file__).resolve().parents[1] / "hip_id_agent" / "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "PortalLearningRuntime" in source
    assert "await _learning_begin(phase, attempt_no, phase_dir, contract)" in source
    assert "await _learning_finish(" in source
    assert '"additional_browser_controller_started": False' in source
