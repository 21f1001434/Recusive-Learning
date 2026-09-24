"""V243R14: Document Type must fill the whole form, not only the top section.

Live symptom: on Source Document Type the agent filled Name, Transaction Type,
Data Format Type and Description, then asked for human feedback. Operation,
identifier Derived From/Value, every attribute Derived From/Usage/Expression
and Validation Type stayed empty.

Causes, reproduced on a full-length DDS replica
(``fixtures/document_type_full_dds.html``):

* After a parent committed, the child-visibility gate waited for *every* node
  that listed it in ``depends_on``.  The dependency contract adds ordering-only
  edges (section and row sequence) and ancestor closure, so Name and Data
  Format Type waited for the identifier Value and attribute Expressions, which
  only appear after their own Derived From is chosen.  The gate always failed.
* Any failure was then cascaded through the ordering edges, so every later
  section was skipped as "dependency failed" and each adaptive cycle repeated
  the same failure until the mission asked a human.
* Controls below the fold failed the elementFromPoint hit test because nothing
  scrolled them into view first ("target center intercepted").
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_dependency_runtime import apply_dependency_execution_contract
from hip_id_agent.autonomous_form_runtime import autonomous_target_execution, execute_autonomous_phase_goal
from hip_id_agent.form_interaction_policy import (
    eligible_child_nodes,
    ordering_only_dependencies,
    structural_dependencies,
    universal_locator_preflight,
)
from hip_id_agent.stateful_form_runtime import compile_phase_state_graph, execute_document_type_state_graph

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "document_type_full_dds.html"
PHASE = "source_document_type"


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _document_type(attribute_rows: int | None = None) -> Dict[str, Any]:
    payload = json.loads((ROOT / "examples" / "uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))
    obj = dict((payload.get("objects") or payload)[PHASE])
    if attribute_rows is not None:
        obj["attributes_to_configure"] = list(obj["attributes_to_configure"])[:attribute_rows]
    return {"objects": {PHASE: obj}}


def _contract_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    return apply_dependency_execution_contract(compile_phase_state_graph(data, PHASE), phase=PHASE)


def _node(graph: Dict[str, Any], field: str, row: int | None = None) -> Dict[str, Any]:
    return next(n for n in graph["nodes"] if n["field_key"] == field and n.get("row_index") == row)


# --------------------------------------------------------------------------- graph semantics


def test_child_gate_waits_only_for_children_this_commit_reveals():
    graph = _contract_graph(_document_type())
    name, fmt = _node(graph, "document_type_name"), _node(graph, "data_format_type")
    status = {name["node_id"]: True}
    # Name only orders the next section; it reveals nothing.
    assert eligible_child_nodes(graph, name["node_id"], status) == []
    status[fmt["node_id"]] = True
    fmt_children = {n["field_key"] for n in eligible_child_nodes(graph, fmt["node_id"], status)}
    # Data Format Type reveals Operation.  Value and Expression are grandchildren
    # revealed later by their own Derived From, so they must not be awaited here.
    assert "document_identifier_operation" in fmt_children
    assert not fmt_children & {"document_identifier_value", "attribute_expression"}

    for key in ("document_identifier_operation",):
        status[_node(graph, key)["node_id"]] = True
    id_derived = _node(graph, "document_identifier_derived_from", 0)
    status[id_derived["node_id"]] = True
    assert [n["field_key"] for n in eligible_child_nodes(graph, id_derived["node_id"], status)] == ["document_identifier_value"]


def test_only_structural_parent_failure_skips_a_field():
    graph = _contract_graph(_document_type())
    validation = _node(graph, "validation_type")
    fmt = _node(graph, "data_format_type")
    # Validation Type is ordered after the attributes but is revealed only by
    # Data Format Type.
    assert structural_dependencies(graph, validation) == [fmt["node_id"]]
    assert any("attribute" in d for d in ordering_only_dependencies(graph, validation))
    expression = _node(graph, "attribute_expression", 0)
    assert _node(graph, "attribute_derived_from", 0)["node_id"] in structural_dependencies(graph, expression)
    # Without contract edges every dependency stays structural (fail closed).
    bare = {"nodes": [dict(expression)], "dependency_edges": []}
    assert structural_dependencies(bare, expression) == [str(d) for d in expression["depends_on"]]


# --------------------------------------------------------------------------- live replica


async def _run_replica(
    tmp_path: Path, data: Dict[str, Any], *, max_cycles: int, defer_operation: bool = False, broker_session: bool = False,
) -> tuple[Dict[str, Any], List[Any]]:
    obj = data["objects"][PHASE]
    setup = f"window.__attributeRows = {len(obj['attributes_to_configure'])};"
    if defer_operation:
        setup += "window.__deferOperationOptions = true;"
    # The replica renders exactly the attribute rows the input configures, so no
    # required portal field is left without an input.json value.
    html = FIXTURE.read_text(encoding="utf-8").replace("<script>", f"<script>{setup}</script><script>", 1)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
        except Exception as exc:  # pragma: no cover - environment without Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        # 720px high: identifier rows, attributes and validation start below the fold.
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content(html)
        if broker_session:
            from hip_id_agent.browser_session import BrowserSession
            from hip_id_agent.config import AppConfig

            cfg = AppConfig()
            # Keep learned website memory out of the repository's data/hip_memory.
            cfg.reporting.memory_dir = str(tmp_path / "hip_memory")
            session = BrowserSession(cfg, tmp_path / "session")
            session.page = page
            session._active_phase_name = PHASE

            async def _active_page(expected_url: str = ""):
                return page

            session._ensure_active_page = _active_page
            # The live MCP evidence servers are not running in unit tests.
            if getattr(session, "semantic_action_gate", None) is not None:
                session.semantic_action_gate.enabled = False
            page._hip_browser_session = session
        try:
            result = await execute_autonomous_phase_goal(
                page=page, graph=compile_phase_state_graph(data, PHASE), phase=PHASE,
                input_data=data, config=None, output_dir=tmp_path / "out", max_cycles=max_cycles,
                executor=execute_document_type_state_graph,
            )
            dom = await page.evaluate(
                """() => Array.from(document.querySelectorAll('fieldset')).map(fs => ({
                    legend: fs.querySelector('legend').textContent.trim(),
                    fields: Array.from(fs.querySelectorAll('input,textarea')).map(e => ({
                        key: e.placeholder || e.name,
                        value: e.type === 'checkbox' ? e.checked : e.value,
                        tags: e.closest('dds-dropdown') ? Array.from(e.closest('dds-dropdown').querySelectorAll('.dds__tag')).map(t => t.textContent) : [],
                    })),
                }))"""
            )
            return result, dom
        finally:
            await browser.close()


def _assert_portal_matches_input(dom: List[Dict[str, Any]], obj: Dict[str, Any]) -> None:
    sections = {s["legend"]: s["fields"] for s in dom}
    details = {f["key"]: f["value"] for f in sections["Document Type Details"]}
    assert details["Name"] == obj["name"]
    assert details["Data Format Type"] == obj["data_format_type"]
    identifier = {f["key"]: f["value"] for f in sections["Document Identifier"]}
    assert identifier["Operation"] == obj["document_identifier"]["operation"]
    assert identifier["Derived From"] == "Transaction Root Element"
    assert identifier["Value"] == obj["document_identifier"]["rows"][0]["value"]
    fields = sections["Attributes To Configure"]
    rows: List[Dict[str, Any]] = []
    for f in fields:
        if f["key"] == "Attribute Name":
            rows.append({"name": f["value"]})
        else:
            rows[-1][f["key"]] = f
    labels = {"ELEMENT_IN_PAYLOAD": "Element In Payload", "TRANSACTION_ROOT_ELEMENT": "Transaction Root Element"}
    for row, expected in zip(rows, obj["attributes_to_configure"]):
        assert row["name"] == expected["attribute_name"]
        assert row["Derived From"]["value"] == labels[expected["derived_from"]]
        assert row["Usage"]["tags"] == [x.strip() for x in expected["usage"].split(",")]
        if expected["expression"]:
            assert row["Expression/Value"]["value"] == expected["expression"]
        else:
            assert "Expression/Value" not in row
    assert {f["key"]: f["value"] for f in sections["Validation"]}["Validation Type"] == obj["validation_type"]


def test_full_document_type_form_is_filled_and_proven_in_one_cycle(tmp_path: Path):
    data = _document_type()
    result, dom = asyncio.run(_run_replica(tmp_path, data, max_cycles=2))
    final = autonomous_target_execution(result)
    failed = [(a.get("field"), a.get("row_index"), a.get("reason")) for a in final.get("attempts", []) if not a.get("success")]
    assert result["status"] == "pass", failed
    assert [c.get("status") for c in result.get("cycles", [])] == ["goal_achieved"]
    fields = {a["field"] for a in final["attempts"]}
    assert {
        "document_identifier_operation", "document_identifier_derived_from", "document_identifier_value",
        "attribute_derived_from", "attribute_usage", "attribute_expression", "validation_type",
    } <= fields
    _assert_portal_matches_input(dom, data["objects"][PHASE])


def test_full_form_through_the_live_browser_session_broker(tmp_path: Path):
    # Same flow through BrowserSession.click_and_wait/fill_and_log, whose
    # universal preflight hit-tests every target before the physical action.
    data = _document_type(attribute_rows=2)
    result, dom = asyncio.run(_run_replica(tmp_path, data, max_cycles=2, broker_session=True))
    final = autonomous_target_execution(result)
    failed = [(a.get("field"), a.get("row_index"), a.get("reason")) for a in final.get("attempts", []) if not a.get("success")]
    assert result["status"] == "pass", failed
    _assert_portal_matches_input(dom, data["objects"][PHASE])


def test_agent_repairs_a_late_dropdown_itself_without_human_help(tmp_path: Path):
    # Operation's options arrive late (only after the rest of the form is set).
    # The fill pass must still fill every field that does not depend on
    # Operation; the cycle's full-graph repair pass then fixes Operation and its
    # identifier row without asking a human.
    data = _document_type(attribute_rows=2)
    result, dom = asyncio.run(_run_replica(tmp_path, data, max_cycles=3, defer_operation=True))
    assert result["status"] == "pass", result.get("reason")
    cycle = result["cycles"][0]
    fill_pass = {
        (a.get("field"), a.get("row_index")): a
        for a in (cycle.get("non_file_execution") or {}).get("attempts", [])
    }
    assert fill_pass[("document_identifier_operation", None)]["success"] is False
    assert "dependency failed" in fill_pass[("document_identifier_derived_from", 0)]["reason"]
    for key in [("validation_type", None), ("attribute_derived_from", 0), ("attribute_usage", 1), ("attribute_expression", 1)]:
        assert fill_pass[key]["success"] is True, (key, fill_pass[key].get("reason"))
    repair_pass = {
        (a.get("field"), a.get("row_index")): a
        for a in (result["cycles"][-1].get("full_goal_execution") or {}).get("attempts", [])
    }
    for key in [("document_identifier_operation", None), ("document_identifier_derived_from", 0), ("document_identifier_value", 0)]:
        assert repair_pass[key]["success"] is True, (key, repair_pass[key].get("reason"))
    _assert_portal_matches_input(dom, data["objects"][PHASE])


def test_universal_preflight_scrolls_a_below_the_fold_target_into_view():
    html = """<!doctype html><html><head><style>html{scroll-behavior:smooth}
      header{position:fixed;top:0;left:0;right:0;height:60px;background:#123;z-index:10}</style></head>
      <body><header>HIP</header><div style="height:2200px"></div>
      <input id="late" placeholder="Validation Type" role="combobox"></body></html>"""

    async def run():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1000, "height": 700})
            await page.set_content(html)
            try:
                return await universal_locator_preflight(page, page.locator("#late"), action="click", selector="#late")
            finally:
                await browser.close()

    audit = asyncio.run(run())
    assert audit["pass"] is True, audit.get("reason")
    assert audit["scroll_into_view"]["scrolled"] is True and audit["hit_test"]["pass"] is True
