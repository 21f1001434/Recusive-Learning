import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.playwright_mcp import PlaywrightMCPBackend
from hip_id_agent.form_knowledge_plan import compile_phase_plan


class FakeClient:
    def __init__(self, schemas):
        self.tools = {name: SimpleNamespace(input_schema=schema) for name, schema in schemas.items()}
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}]}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_playwright_mcp_uses_current_target_schema(tmp_path: Path):
    client = FakeClient({
        "browser_click": {"properties": {"element": {}, "target": {}}},
    })
    backend = PlaywrightMCPBackend(client, tmp_path)
    await backend.click("input#field", element="Profile Name")
    assert client.calls == [("browser_click", {"target": "input#field", "element": "Profile Name"})]


@pytest.mark.asyncio
async def test_playwright_mcp_supports_older_ref_schema(tmp_path: Path):
    client = FakeClient({
        "browser_type": {"properties": {"element": {}, "ref": {}, "text": {}, "submit": {}}},
    })
    backend = PlaywrightMCPBackend(client, tmp_path)
    await backend.fill("e42", "UHAUL", element="Partner field")
    name, args = client.calls[0]
    assert name == "browser_type"
    assert args["ref"] == "e42"
    assert args["text"] == "UHAUL"
    assert args["submit"] is False


def test_plan_is_compiled_from_learned_form_knowledge_and_current_input():
    learned = {
        "verification_status": "pass",
        "field_steps": [
            {
                "key": "flow_description",
                "label": "Flow Description",
                "selector": "textarea#old-generated-id",
                "fallback_label": "Flow Description",
                "tab": "Basic Details",
                "value": "old value",
                "fill_strategy": "type_or_set_value",
                "required": True,
            },
            {
                "key": "process_step_type",
                "label": "Process Step Type",
                "selector": "input#step-type",
                "tab": "Target Details",
                "value": "Mapping Transformer",
                "fill_strategy": "select_or_type",
                "required": True,
            },
        ],
        "evidence_files": ["bizflow_form_kb.json"],
    }
    payload = {
        "objects": {
            "biz_flow": {
                "description": "new current-input description",
                "configure_targets": [{"process_steps": [{"type": "Mapping Transformer"}]}],
            }
        }
    }
    plan = compile_phase_plan(
        phase="biz_flow",
        payload=payload,
        learned_blueprint=learned,
        blueprint_path=Path("previous/biz_flow_fast_fill_blueprint.json"),
    )
    assert plan["plan_source"].startswith("HIP Portal learned form knowledge")
    assert plan["actions"][0]["expected_value"] == "new current-input description"
    assert plan["actions"][0]["executor"] == "playwright-mcp"
    assert "parent:Process Step Type selected and dependency controls rendered" not in plan["actions"][0]["preconditions"]
    assert plan["actions"][1]["expected_value"] == "Mapping Transformer"
    assert "row:Process Step exists and accordion expanded" in plan["actions"][1]["preconditions"]


def test_plan_includes_effect_validated_repeatable_rows_and_section_gates():
    learned = {
        "verification_status": "pass",
        "repeatable_section_plan": [
            {
                "section": "Process Steps",
                "input_path": "objects.biz_flow.process_steps",
                "tab": "Configure Target",
                "row_count_from_input": 2,
                "aliases": ["process step", "mapping transformer", "enricher"],
                "row_values": [{"type": "Mapping Transformer"}, {"type": "Enricher"}],
            }
        ],
        "field_steps": [
            {
                "key": "process_step_type",
                "label": "Process Step Type",
                "selector": "input#step-type",
                "tab": "Configure Target",
                "value": "Mapping Transformer",
                "fill_strategy": "select_or_type",
                "required": True,
            }
        ],
        "evidence_files": ["bizflow_form_kb.json", "bizflow_tab_form_kb.json"],
    }
    payload = {"objects": {"biz_flow": {"process_steps": [{"type": "Mapping Transformer"}, {"type": "Enricher"}]}}}
    plan = compile_phase_plan(
        phase="biz_flow",
        payload=payload,
        learned_blueprint=learned,
        blueprint_path=Path("previous/biz_flow_fast_fill_blueprint.json"),
    )
    row_action = next(a for a in plan["actions"] if a["operation"] == "ensure_repeatable_row_count")
    assert row_action["expected_row_count"] == 2
    assert row_action["executor"] == "playwright-mcp"
    assert "count must increase" in row_action["effect_validation"]["after"]
    gate = next(a for a in plan["actions"] if a["operation"] == "judge_section_and_block_on_failure")
    assert gate["expected_value"] == "pass"
    assert "Playwright MCP accessibility snapshot" in gate["verification"]
    assert plan["form_knowledge_contract"]["not_llm_invented"] is True
