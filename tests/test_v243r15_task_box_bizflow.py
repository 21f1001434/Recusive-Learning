"""V243R15: "Create biz flow ... from input.json" from the task box, end to end.

The task box recognises the BizFlow object, walks the four wizard tabs itself,
opens the Configure Routing rule drawer with "+ Add", and proves every section
with the same autonomous goal as a mission.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.universal_portal_operator import UniversalPortalTaskExecutor
from phase_replica_support import ROOT, chromium_path, replica_html


class _Browser:
    def __init__(self, page):
        self.page = page

    async def resolve_semantic_affordance(self, **_kwargs):
        return {"resolved": False}


def test_task_box_creates_the_whole_biz_flow_across_all_tabs(tmp_path: Path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    input_json = tmp_path / "input.json"
    input_json.write_text((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"), encoding="utf-8")
    executor = UniversalPortalTaskExecutor(cfg, HIPCapabilityGraph(tmp_path / "graph"))

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(replica_html("bizflow_wizard_dds.html"))
            try:
                result = await executor._fill_from_input(
                    _Browser(page), task="Create biz flow U-HAUL from input.json", input_json=str(input_json),
                    input_root="", run_dir=tmp_path / "run", max_cycles=2,
                )
                routing_target = await page.evaluate(
                    "() => { const d = document.querySelector('.dds__drawer'); const i = d && Array.from(d.querySelectorAll('input')).find(x => x.placeholder === 'Target *'); return i ? i.value : ''; }"
                )
                return result, routing_target
            finally:
                await browser.close()

    result, routing_target = asyncio.run(run())
    assert result["pass"] is True, json.dumps(result.get("sections"), default=str)[:2000]
    assert result["hip_phase"] == "biz_flow"
    assert result["verification"] == "100_percent_runtime_input_exact_readback"
    sections = result["sections"]
    assert [s["section"] for s in sections] == ["Flow Details", "Configure Source", "Configure Target(s)", "Configure Routing"]
    assert all(s["pass"] and s["navigation"]["tab"]["clicked"] for s in sections)
    assert sections[-1]["navigation"]["routing_add"]["clicked"] is True
    assert routing_target == "SFTP_U-HAUL_ASN_PC_TGT_OB"
