"""V243R13: a user task from the Control Center must fill, verify and learn.

``/api/portal-task/run`` -> ``UniversalPortalTaskExecutor._fill_from_input``
requires ``authoritative_execution_verified is True``. Before R13 the executor
result was masked to ``"***MASKED***"``, so every form-filling task failed with
"Universal input fill did not reach 100% exact runtime-input coverage" and no
skill/recipe/replay knowledge was ever learned from it.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.universal_portal_operator import UniversalPortalTaskExecutor

FORM = """<!doctype html><html><body><div class="dds__drawer" role="dialog"><h2>Create Partner Contact</h2>
<form><fieldset><legend>Contact Details</legend>
<div class="dds__form-group"><label for="contactName">Contact Name</label><input id="contactName" name="contactName" formcontrolname="contactName" type="text"></div>
<div class="dds__form-group"><label for="contactEmail">Contact Email</label><input id="contactEmail" name="contactEmail" formcontrolname="contactEmail" type="text"></div>
<div class="dds__form-group"><label for="region">Region</label><input id="region" name="region" formcontrolname="region" type="text"></div>
</fieldset><button type="button">Cancel</button></form></div></body></html>"""


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


class _Browser:
    def __init__(self, page):
        self.page = page

    async def resolve_semantic_affordance(self, **_kwargs):
        return {"resolved": False}


def test_universal_task_fill_is_exact_and_learnable(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({"objects": {"partner_contact": {
        "contact_name": "Jordan Rivera", "contact_email": "jordan@example.com", "region": "EMEA",
    }}}), encoding="utf-8")
    executor = UniversalPortalTaskExecutor(cfg, HIPCapabilityGraph(tmp_path / "graph"))

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(FORM)
            try:
                result = await executor._fill_from_input(
                    _Browser(page), task="fill partner contact from input", input_json=str(input_json),
                    input_root="$.objects.partner_contact", run_dir=tmp_path / "run", max_cycles=2,
                )
                values = [await page.locator(f"#{i}").input_value() for i in ("contactName", "contactEmail", "region")]
                return result, values
            finally:
                await browser.close()

    result, values = asyncio.run(run())
    assert values == ["Jordan Rivera", "jordan@example.com", "EMEA"]
    assert result["pass"] is True, result
    assert result["verification"] == "100_percent_runtime_input_exact_readback"
    assert result["cycles"][0]["exact_execution_verified"] is True
    # The verified blueprint is what skill induction learns from (value-free).
    blueprint = json.dumps(result["skill_blueprint"])
    assert "Jordan" not in blueprint and "example.com" not in blueprint
