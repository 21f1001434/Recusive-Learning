from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract
from hip_id_agent.config import AppConfig
from hip_id_agent.maximum_observability import (
    MaximumObservabilityCollector,
    build_input_coverage_contract,
    build_replay_readiness_report,
    flatten_input_paths,
    sanitize_dom_structure,
)
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph


def _input() -> dict:
    return json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def test_flatten_input_paths_masks_secrets_and_ignores_runtime_metadata():
    result = flatten_input_paths({
        "name": "demo",
        "password": "do-not-store",
        "rows": [{"value": "A"}, {"value": "B"}],
        "_mission_prior_entities": {"id": 10},
    })
    assert result["$.name"] == "demo"
    assert result["$.password"] == "***MASKED***"
    assert result["$.rows[1].value"] == "B"
    assert not any("_mission_prior_entities" in path for path in result)


def test_sanitized_dom_preserves_structure_but_removes_values_scripts_and_queries():
    html = """
    <html><body>
      <script>window.token='secret'</script>
      <form><input formcontrolname="target" value="CUSTOMER-VALUE" aria-controls="list-1">
      <textarea>private text</textarea><a href="https://example.test/a?token=abc">Open</a></form>
    </body></html>
    """
    result = sanitize_dom_structure(html)
    assert "<script" not in result
    assert "CUSTOMER-VALUE" not in result
    assert "private text" not in result
    assert "formcontrolname=\"target\"" in result
    assert "aria-controls=\"list-1\"" in result
    assert "?token=" not in result
    assert "VALUE_REDACTED" in result


def test_every_full_phase_has_complete_required_input_control_mapping():
    payload = _input()
    phases = [
        "data_map", "source_document_type", "target_document_type", "rule",
        "source_transport_profile", "target_transport_profile", "biz_flow",
    ]
    for phase in phases:
        graph = apply_dependency_execution_contract(compile_phase_state_graph(payload, phase), phase=phase)
        coverage = build_input_coverage_contract(
            phase=phase,
            input_payload=payload,
            state_graph=graph,
            verification={"status": "passed"},
        )
        assert coverage["pass"] is True, (phase, coverage["missing_required_input_paths"])
        assert coverage["required_actionable_node_count"] > 0


def test_input_control_coverage_fails_when_required_node_has_no_input_path():
    coverage = build_input_coverage_contract(
        phase="demo",
        input_payload={"value": "x"},
        state_graph={
            "dependency_execution_contract": {"pass": True},
            "nodes": [{
                "node_id": "demo.required",
                "field_key": "required",
                "section": "Details",
                "action": "fill_text",
                "required": True,
                "input_path": "",
            }],
        },
        verification={"status": "passed"},
    )
    assert coverage["pass"] is False
    assert coverage["missing_required_input_paths"][0]["node_id"] == "demo.required"


def test_replay_readiness_is_fail_closed_without_dependency_or_coverage_proof():
    report = build_replay_readiness_report(
        phase="rule",
        coverage={"pass": False},
        dependency_contract={"pass": False, "cycle_node_ids": ["a"]},
        verification={"status": "failed"},
        evidence_channels={"local_playwright": {"available": True}},
    )
    assert report["pass"] is False
    assert report["checks"]["input_control_coverage"] is False
    assert report["checks"]["acyclic_dependency_contract"] is False


def test_maximum_observability_is_enabled_by_default():
    cfg = AppConfig()
    assert cfg.portal_learning.maximum_observability_enabled is True
    assert cfg.portal_learning.capture_sanitized_dom_structure is True
    assert cfg.portal_learning.maximum_observability_max_controls >= 5000


class _FakePage:
    url = "https://example.test/rules"

    async def evaluate(self, script, arg=None):
        if "__hipMaximumObservability" in script and "MutationObserver" in script:
            return {"status": "installed", "phase": arg["phase"], "attempt": arg["attempt"]}
        return {
            "url": self.url,
            "title": "Rules",
            "controls": [{"visible": True, "tag": "input", "role": "", "type": "text", "formControlName": "ruleName", "label": "Name", "required": True, "section": "Rule"}],
            "forms": [], "sections": [], "actions": [], "resources": [], "navigationTiming": [],
            "links": [], "alerts": [], "popups": [], "storageKeyNames": {"localStorage": [], "sessionStorage": []},
            "observer": {"mutations": [{"type": "attributes"}], "events": [{"type": "change"}]},
        }

    async def content(self):
        return '<form><input formcontrolname="ruleName" value="secret"></form>'


class _FakeBrowser:
    pass


@pytest.mark.asyncio
async def test_maximum_observability_collector_writes_structural_bundle(tmp_path):
    cfg = AppConfig()
    collector = MaximumObservabilityCollector(config=cfg, root_dir=tmp_path, browser=_FakeBrowser())
    page = _FakePage()
    installed = await collector.install_observers(page, phase="rule", attempt=1)
    assert installed["status"] == "installed"
    graph = {
        "dependency_execution_contract": {"pass": True, "ordered_node_ids": ["rule.name"], "cycle_node_ids": []},
        "nodes": [{"node_id": "rule.name", "field_key": "rule_name", "action": "fill_text", "required": True, "input_path": "$.objects.rule.name"}],
    }
    out = tmp_path / "evidence"
    result = await collector.capture(
        page=page,
        phase="rule",
        stage="success",
        output_dir=out,
        input_payload={"objects": {"rule": {"name": "demo"}}},
        state_graph=graph,
        verification={"status": "passed"},
        evidence_channels={"local_playwright": {"available": True}},
    )
    assert result["coverage_pass"] is True
    assert result["replay_ready"] is True
    assert (out / "maximum_page_intelligence.json").is_file()
    assert (out / "sanitized_dom_structure.html").is_file()
    assert "secret" not in (out / "sanitized_dom_structure.html").read_text(encoding="utf-8")
