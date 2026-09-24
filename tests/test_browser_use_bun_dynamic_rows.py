from __future__ import annotations

import json
from pathlib import Path

import pytest

from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.repeatable_rows import apply_repeatable_row_adds, build_repeatable_section_plan


def _two_rule_rows_input():
    return {
        "objects": {
            "rule": {
                "conditions": {
                    "rows": [
                        {"condition_type": "Attributes", "operator": "Equals", "value": "uhaul", "attribute_name": "Receiver"},
                        {"condition_type": "Attributes", "operator": "Contains", "value": "DELL", "attribute_name": "Sender"},
                    ]
                }
            }
        }
    }


def test_two_json_rule_rows_plan_requires_exactly_one_plus_click():
    plan = build_repeatable_section_plan(_two_rule_rows_input(), "rule")
    cond = next(p for p in plan if p["section"] == "Rule Conditions")
    assert cond["row_count_from_input"] == 2
    assert cond["add_clicks_needed"] == 1
    assert cond["row_values"][1]["operator"] == "Contains"
    assert cond["row_values"][1]["value"] == "DELL"


@pytest.mark.asyncio
async def test_generic_repeatable_engine_clicks_plus_once_for_two_rows(monkeypatch):
    import hip_id_agent.repeatable_rows as rr

    counts = {"value": 1, "clicks": 0}

    async def fake_count(page, section):
        return counts["value"]

    async def fake_find(page, aliases, phase=""):
        return {"selector": "button#conditions-plus", "label": "+", "source": "test"}

    class Loc:
        async def count(self): return 1
        async def is_visible(self, timeout=None): return True
        async def is_enabled(self, timeout=None): return True
        async def click(self, timeout=None):
            counts["clicks"] += 1
            counts["value"] += 1

    class LocatorWrapper:
        @property
        def first(self): return Loc()

    class Page:
        def locator(self, selector): return LocatorWrapper()
        async def wait_for_timeout(self, ms): return None

    monkeypatch.setattr(rr, "_repeatable_row_count", fake_count)
    monkeypatch.setattr(rr, "_find_safe_add_button", fake_find)
    audit = await apply_repeatable_row_adds(Page(), _two_rule_rows_input(), "rule")
    assert counts["clicks"] == 1
    assert counts["value"] == 2
    assert audit["summary"]["planned_add_clicks"] == 1
    assert audit["summary"]["clicked"] == 1
    assert audit["summary"]["failed"] == 0
    assert audit["summary"]["exact_row_count_pass"] is True


def test_bun_and_browser_use_are_shipped_as_defaults():
    cfg = load_config("config.yaml")
    assert cfg.mcp.playwright_mcp_command == "auto"
    assert cfg.mcp.chrome_devtools_command == "auto"
    assert cfg.browser_use.enabled is True
    assert cfg.browser_use.attach_same_browser is True
    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    assert "@playwright/mcp" in package["devDependencies"]
    assert "chrome-devtools-mcp" in package["devDependencies"]


def test_browser_use_dependency_and_python_floor():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in pyproject
    assert '"browser-use==0.13.8"' in pyproject
    assert "browser-use==0.13.8" in requirements


def test_browser_use_bridge_is_state_only_not_free_form_agent():
    src = Path("hip_id_agent/browser_use_bridge.py").read_text(encoding="utf-8")
    assert "from browser_use import Browser" in src
    assert "cdp_url=self.cdp_url" in src
    assert "Agent(" not in src
    assert "state_snapshot" in src


def test_bun_live_runner_and_dashboard_repeatable_preview():
    runner = Path("run_live_full_dummy.ps1").read_text(encoding="utf-8")
    dashboard = Path("hip_id_agent/streamlit_dashboard.py").read_text(encoding="utf-8")
    assert 'Assert-Command "bun"' in runner
    assert 'Assert-Command "bunx"' in runner
    assert "bun install" in runner
    assert "collect_repeatable_row_plan" in dashboard
    assert "Input-driven + row plan" in dashboard


def test_browser_session_opens_cdp_when_browser_use_enabled():
    src = Path("hip_id_agent/browser_session.py").read_text(encoding="utf-8")
    assert "needs_local_cdp" in src
    assert "browser_use_cfg" in src
    assert "--remote-debugging-port=" in src


def test_shipped_uhaul_input_exposes_all_multirow_plus_requirements():
    from hip_id_agent.streamlit_dashboard import collect_repeatable_row_plan

    rows = collect_repeatable_row_plan("examples/uhaul_poasn_full_dummy_input.json")
    by_key = {(r["phase"], r["section"]): r for r in rows}
    assert by_key[("rule", "Rule Conditions")]["json_rows"] == 2
    assert by_key[("rule", "Rule Conditions")]["plus_clicks_from_one_initial_row"] == 1
    assert by_key[("biz_flow", "Flow Identifier Conditions")]["plus_clicks_from_one_initial_row"] == 1
    assert by_key[("biz_flow", "Process Steps")]["plus_clicks_from_one_initial_row"] == 1
    assert by_key[("biz_flow", "Routing Conditions")]["plus_clicks_from_one_initial_row"] == 1
    assert by_key[("source_document_type", "Attributes To Configure")]["json_rows"] == 5
    assert by_key[("source_document_type", "Attributes To Configure")]["plus_clicks_from_one_initial_row"] == 4
