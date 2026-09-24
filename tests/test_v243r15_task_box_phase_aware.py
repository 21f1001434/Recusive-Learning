"""V243R15: the Control Center task box uses the learned HIP phase knowledge.

Before, ``fill_from_input`` bound input.json leaves to controls one by one with
no dependency order, radio groups or repeatable-row kinds.  On the Transport
Profile form that left System Name, both Yes/No groups, Existing Account Name
and Subscription Folder unresolved and asked for human help.  A task that names
a HIP object (or targets its input root / open form) now runs that phase's
compiled graph through the same hardened autonomous goal as a mission.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

import hip_id_agent.universal_portal_operator as operator
from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from phase_replica_support import ROOT, chromium_path, replica_html

PAYLOAD = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("task,root,heading,expected", [
    ("Create the target transport profile from input.json", "", "", "target_transport_profile"),
    ("create a new rule from input json", "", "", "rule"),
    ("fill form", "$.objects.source_document_type", "", "source_document_type"),
    ("create biz flow U-HAUL", "", "", "biz_flow"),
    ("fill the data map form", "", "", "data_map"),
    ("fill this form", "", "Create Rule", "rule"),
    # Source or target is ambiguous: keep the generic binder rather than guess.
    ("fill the transport profile form", "", "Create Transport Profile", ""),
    ("open settings and change the theme", "", "", ""),
])
def test_task_is_routed_to_its_hip_phase(task, root, heading, expected):
    assert operator.resolve_hip_phase_for_task(task=task, input_root=root, payload=PAYLOAD, heading=heading) == expected


class _Browser:
    def __init__(self, page):
        self.page = page

    async def resolve_semantic_affordance(self, **_kwargs):
        return {"resolved": False}


def _fill(tmp_path: Path, *, legacy: bool, monkeypatch) -> tuple[dict, list]:
    if legacy:
        monkeypatch.setattr(operator, "resolve_hip_phase_for_task", lambda **_k: "")
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    executor = operator.UniversalPortalTaskExecutor(cfg, HIPCapabilityGraph(tmp_path / "graph"))

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(replica_html("transport_profile_full_dds.html"))
            try:
                result = await executor._fill_from_input(
                    _Browser(page), task="Create the source transport profile from input.json",
                    input_json=str(input_json), input_root="", run_dir=tmp_path / "run", max_cycles=2,
                )
                radios = await page.evaluate("() => Array.from(document.querySelectorAll('input[type=radio]:checked')).map(r => r.name + '=' + r.value)")
                return result, radios
            finally:
                await browser.close()

    return asyncio.run(run())


def test_task_box_fills_the_transport_profile_with_phase_knowledge(tmp_path: Path, monkeypatch):
    result, radios = _fill(tmp_path, legacy=False, monkeypatch=monkeypatch)
    assert result["pass"] is True, result
    assert result["hip_phase"] == "source_transport_profile"
    assert result["verification"] == "100_percent_runtime_input_exact_readback"
    assert sorted(radios) == ["existingAccount=true", "useExistingFolder=false"]
    # What skill induction learns from the task is value-free.
    blueprint = json.dumps(result["skill_blueprint"])
    assert result["skill_blueprint"]["field_count"] >= 14
    assert "haftatap10251108" not in blueprint and "SFTP_U-HAUL" not in blueprint


def test_generic_binder_alone_could_not_do_this_task(tmp_path: Path, monkeypatch):
    result, _ = _fill(tmp_path, legacy=True, monkeypatch=monkeypatch)
    assert result["pass"] is False
    unresolved = {p.rsplit(".", 1)[-1] for p in result["unresolved_input_leaves"]}
    assert {"existing_account", "use_existing_folder"} <= unresolved
