"""V243R20: every input.json row is created with the portal's own "+" and filled.

Live report: the agent highlighted the right fields but left sections short --
where the portal needs "+" clicks to add rows (Document Type attributes, Rule
conditions, BizFlow flow identifiers, process steps, file-name parts, routing
conditions) the rows were never created.

On the live portal that "+" is an icon-only ``dds-button`` in the list's
legend: a ``span.dds__icon--add-cir``, named only by a hover tooltip ("Create
Condition"); every row carries a remove icon, and lists start with one row or
none.  The replicas' ``window.__livePlus`` mode renders exactly that
(``hip_dds_kit.js`` ``liveList``/``plusButton``), and a click that lands while a
dropdown popup is still open is swallowed, as DDS does.

Root causes fixed:

* the row adder recognised only "plus"/"add-circle" icons and "+ Add" text, and
  rejected any label with "create" -- the live plus (``add-cir``, tooltip
  "Create Condition") was never a candidate (``no_add_control_found``);
* with no row on screen yet (process steps), an icon-only plus was never allowed;
* rows added by "+" have no labels, so their kind was read from the FormArray
  name ("attributes") or nearby text ("routing_action") instead of their list's
  ("flow_identifier", "routing_condition"): the new row was not counted, the
  click looked ineffective and the row stayed empty;
* the broker label "Create Condition row" read as a final mutation to the
  safety guards.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.form_structure_healer import _ADD_BUTTON_JS, _BLOCKED_WORDS, add_row_action_label, ensure_repeatable_rows
from phase_replica_support import FIXTURES, chromium_path, dom_value, failed_fields, run_phase_replica, uhaul_input


def _stats(result):
    return result.get("live_plus_stats") or {}


def test_document_type_identifier_and_attribute_rows_are_added_with_their_own_legend_plus(tmp_path: Path):
    data = uhaul_input("source_document_type", tmp_path)
    obj = data["objects"]["source_document_type"]
    obj["document_identifier"] = {"operation": "All conditions are satisfied", "rows": [
        {"derived_from": "TRANSACTION_ROOT_ELEMENT", "value": "DellAutoASN"},
        {"derived_from": "NAMESPACE", "value": "urn:dell:asn"},
    ]}
    result, final, dom = run_phase_replica(
        tmp_path, phase="source_document_type", fixture="document_type_full_dds.html",
        broker=True, live_plus=True, observers=True, max_cycles=3, data=data,
    )
    assert result["status"] == "pass", failed_fields(final)
    names = [d["value"] for d in dom if d.get("key") == "Attribute Name"]
    assert names == ["Receiver", "Sender", "Transaction Type", "Business Identifier", "Alternate Business Identifier"]
    assert [d["value"] for d in dom if d.get("key") == "Value"] == ["DellAutoASN", "urn:dell:asn"]
    # One row of each on screen: 1 identifier + 4 attribute rows added, none removed.
    assert _stats(result) == {"plus_clicks": 5, "rows_removed": 0}


def test_rule_condition_rows_are_added_with_the_conditions_plus_not_the_actions_plus(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="rule", fixture="rule_full_dds.html", broker=True, live_plus=True, observers=True, max_cycles=3,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert [d["value"] for d in dom if d.get("key") == "Value"] == ["uhaul", "DELL"]
    assert len([d for d in dom if d.get("key") == "Name"]) == 2  # rule name + one action: the Actions "+" was not clicked
    assert _stats(result) == {"plus_clicks": 1, "rows_removed": 0}


def test_bizflow_flow_identifier_rows_are_added_in_configure_source(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Source",
        prepare_js="document.getElementById('tab-1').click()", broker=True, live_plus=True, observers=True, max_cycles=3,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert [dom_value(dom, "Value", i)["value"] for i in (0, 1)] == ["uhaul", "DELL"]
    assert _stats(result) == {"plus_clicks": 1, "rows_removed": 0}


def test_bizflow_process_steps_and_nested_file_name_parts_are_created_from_nothing(tmp_path: Path):
    # "No Process Steps Added": both steps come from the legend "+", and the
    # second File Name part from the plus nested inside step 2.
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Target(s)",
        prepare_js="document.getElementById('tab-2').click()", broker=True, live_plus=True, observers=True, max_cycles=3,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert [dom_value(dom, "Step Type", i)["value"] for i in (0, 1)] == ["Mapping Transformer", "Enricher"]
    assert [dom_value(dom, "Part Number *", i)["value"] for i in (0, 1)] == ["1", "2"]
    assert dom_value(dom, "Value *")["value"] == "yyyyddMMhhmmss"
    assert _stats(result) == {"plus_clicks": 3, "rows_removed": 0}


def test_bizflow_routing_condition_rows_are_added_in_the_rule_drawer(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="biz_flow", fixture="bizflow_wizard_dds.html", section="Configure Routing",
        prepare_js="document.getElementById('tab-3').click(); document.getElementById('routing-add').click()",
        broker=True, live_plus=True, observers=True, max_cycles=3,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert [dom_value(dom, "Value", i)["value"] for i in (0, 1)] == ["uhaul", "DELL"]
    assert _stats(result) == {"plus_clicks": 1, "rows_removed": 0}


# ---------------------------------------------------------------- recognition
_PAGE = """<html><body><script>__KIT__</script><script>
const H = window.HIP;
const row = (i) => H.row(H.text({label: 'Value', name: 'value', showLabel: i === 0}), H.text({label: 'Operator', name: 'operator', showLabel: i === 0}));
const form = H.form(
  H.liveList({legend: 'Conditions :', tooltip: 'Create Condition', name: 'conditions', buildRow: row}),
  H.liveList({legend: 'Process Steps :', tooltip: 'Create Process Step', name: 'steps', buildRow: row, initial: 0, empty: 'No Process Steps Added'}),
  H.el('<button type="button" class="dds__button"><span class="dds__icon dds__icon--add-cir"></span> Create</button>'),
  H.el('<button type="button" class="dds__button dds__button--icon-only"><span class="dds__icon dds__icon--chevron-down"></span></button>'),
);
H.page('Create Rule', form);
</script></body></html>"""


async def _candidates(arg):
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page()
        await page.set_content(_PAGE.replace("__KIT__", (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")))
        if arg.get("anchors") == "first_row":
            arg = dict(arg, anchors=["[formarrayname=conditions] input[name=value]"])
        out = await page.evaluate("(arg) => (" + _ADD_BUTTON_JS + ")(document.body, arg)", dict(arg, bad=_BLOCKED_WORDS.pattern))
        await browser.close()
        return out


def test_the_live_legend_plus_is_found_and_the_remove_icon_and_create_button_never_are():
    cands = asyncio.run(_candidates({"anchors": "first_row", "words": ["condition", "conditions"]}))
    assert cands and cands[0]["title"].startswith("Conditions") and cands[0]["where"] == "legend"
    assert cands[0]["label"] == "Create Condition"  # named only by its tooltip
    labels = " ".join(c["label"] for c in cands).lower()
    assert "remove" not in labels and "create" == cands[0]["label"].split()[0].lower()
    assert all("Create" != c["label"] for c in cands)  # a visible "Create" button is never a row adder


def test_an_empty_list_is_grown_only_through_the_plus_in_its_own_legend():
    cands = asyncio.run(_candidates({"anchors": [], "words": ["process", "step"]}))
    assert [c["title"] for c in cands] == ["Process Steps :"]
    none = asyncio.run(_candidates({"anchors": [], "words": ["tag"]}))
    assert none == []  # no row on screen and no plus that names the list: nothing is clicked


def test_the_broker_label_for_a_row_plus_never_reads_as_a_mutation():
    label = add_row_action_label({"title": "Conditions :", "label": "Create Condition"})
    assert label.startswith("structural_opener add row") and "create" not in label.lower()


async def _add_one_condition_row(setup_js: str):
    page_html = _PAGE.replace(
        "H.page('Create Rule', form);",
        "form.prepend(H.row(H.dropdown({label: 'Operation', name: 'operation', options: ['A', 'B']}))); H.page('Create Rule', form);",
    )
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page()
        await page.set_content(page_html.replace("__KIT__", (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")))
        if setup_js == "open-operation-dropdown":
            # A real click: the popup is open and focus is inside the dropdown,
            # as after choosing a value in a multi-select.
            await page.click("dds-dropdown[formcontrolname=operation] input")
        else:
            await page.evaluate(setup_js)

        async def capture():
            return await page.evaluate("""() => Array.from(document.querySelectorAll('[formarrayname=conditions] input[name=value]'))
                .map((e, i) => ({selector: `[formarrayname=conditions] [formgroupname="${i}"] input[name=value]`, label: 'Value',
                                 name: 'value', row_kind: 'condition', row_kind_ordinal: i}))""")

        group = {"group": "kind:condition", "row_kind": "condition", "needed": 2, "names": ["value"],
                 "aliases": ["condition", "conditions"], "context_aliases": []}
        audit = await ensure_repeatable_rows(page, "rule", [group], capture=capture)
        clicks = await page.evaluate("() => window.__hipPlusClicks || 0")
        await browser.close()
        return audit["groups"][0], clicks


def test_an_open_dropdown_is_settled_before_the_plus_is_clicked():
    # The open popup covers the plus and DDS would swallow the click.
    entry, clicks = asyncio.run(_add_one_condition_row("open-operation-dropdown"))
    assert entry["status"] == "rows_created" and entry["live_rows_after"] == 2 and clicks == 1


def test_a_swallowed_plus_click_is_retried_once():
    entry, clicks = asyncio.run(_add_one_condition_row("window.__plusSwallowFirst = true"))
    assert entry["status"] == "rows_created" and entry["live_rows_after"] == 2
    assert clicks == 2 and entry["clicks"][0].get("retry_after_closing_popups", {}).get("closed") is True
