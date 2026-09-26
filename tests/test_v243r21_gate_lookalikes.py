"""V243R21: the live semantic action gate refused every control that has look-alikes.

Live report (Document Type): the agent added all five "Attributes to Configure"
rows and filled Attribute Name, but never filled Derived From or Usage; the
overlay stayed on SELECTING and the phase ran six cycles.

On the live portal every action passes the semantic action gate (it is off in
the replica tests, which have no MCP servers).  The gate identifies a control by
a value-free fingerprint -- label, role, section, framework name -- so a row's
"Derived From" is the same fingerprint in every row, every option of the Usage
multi-select shares the fingerprint "Usage", and a ``dds-button`` host shares
its inner button's.  Two checks then broke on look-alikes:

* ``revalidate`` (just before dispatch) found several matches, and because the
  DOM generation always advances between the target proof and the dispatch --
  the overlay that highlights the target, hover and focus classes -- it refused
  with ``HIP_SEMANTIC_TARGET_DRIFT: ambiguous_after_rerender``: no dropdown in a
  repeated row could even be opened;
* ``verify_and_learn`` (after the action) compared the *first* look-alike, not
  the control acted on, so a multi-select option click that worked was reported
  as ``HIP_SEMANTIC_EFFECT_NOT_PROVEN`` and retried: one option per attempt.

Both now use the executor's own row-exact locator.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.semantic_control import SemanticActionGate
from phase_replica_support import FIXTURES, chromium_path, failed_fields, install_observers, run_phase_replica

_PAGE = """<html><body><script>__KIT__</script><script>
const H = window.HIP;
const rows = [0, 1, 2].map(i => H.row(
  H.dropdown({label: 'Derived From', name: 'derivedFrom', options: ['ELEMENT_IN_PAYLOAD', 'TRANSACTION_ROOT_ELEMENT'], showLabel: i === 0}),
  H.dropdown({label: 'Usage', name: 'usage', options: ['Flow Identifier Expression', 'Logging', 'Mapping', 'Routing'], multiple: true, showLabel: i === 0}),
));
H.page('Create Document Type', H.form(H.fieldset('Attributes to Configure', ...rows)));
</script></body></html>"""


async def _with_page(body):
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=chromium_path())
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium unavailable: {exc}")
        page = await browser.new_page()
        await page.set_content(_PAGE.replace("__KIT__", (FIXTURES / "hip_dds_kit.js").read_text(encoding="utf-8")))
        await install_observers(page)
        try:
            return await body(page)
        finally:
            await browser.close()


async def _advance_generation(page):
    # What the live overlay does when it highlights the selected target.
    await page.evaluate("""() => { const d = document.createElement('div'); d.setAttribute('data-hip-visual-overlay-root', '1');
        d.style.cssText = 'position:fixed;pointer-events:none'; document.documentElement.appendChild(d); }""")
    await page.wait_for_timeout(50)


def _gate(tmp_path: Path) -> SemanticActionGate:
    return SemanticActionGate(None, run_dir=tmp_path / "run", memory_root=tmp_path / "memory")


def test_a_repeated_row_dropdown_is_re_proven_through_the_executors_own_locator(tmp_path: Path):
    async def body(page):
        gate = _gate(tmp_path)
        selector = "dds-dropdown[formcontrolname=derivedFrom] >> nth=2"
        loc = page.locator("dds-dropdown[formcontrolname=derivedFrom] input[role=combobox]").nth(2)
        resolution = await gate.resolve(page=page, locator=loc, action="click", selector=selector,
                                        label="HIP Portal DDS combobox", phase="source_document_type")
        assert resolution["pass"] is True
        await _advance_generation(page)
        old = await gate.revalidate(page=page, resolution=resolution)  # the fingerprint alone
        new = await gate.revalidate(page=page, resolution=resolution, locator=loc)
        return old, new

    old, new = asyncio.run(_with_page(body))
    assert old["pass"] is False and old["status"] == "ambiguous_after_rerender" and old["match_count"] >= 3
    assert new["pass"] is True and new["status"] == "stable_vetted_locator"


def test_a_control_that_was_re_created_is_still_refused(tmp_path: Path):
    async def body(page):
        gate = _gate(tmp_path)
        first_id = await page.evaluate("() => document.querySelectorAll('dds-dropdown[formcontrolname=derivedFrom] input')[1].id")
        loc = page.locator(f"#{first_id}")
        resolution = await gate.resolve(page=page, locator=loc, action="click", selector=f"#{first_id}",
                                        label="HIP Portal DDS combobox", phase="source_document_type")
        # Angular re-creates the row: the proven element is gone.
        await page.evaluate("(id) => { const el = document.getElementById(id); el.id = id + '-new'; }", first_id)
        await _advance_generation(page)
        return await gate.revalidate(page=page, resolution=resolution, locator=loc)

    proof = asyncio.run(_with_page(body))
    assert proof["pass"] is False


def test_a_multi_select_option_click_is_verified_on_the_option_that_was_clicked(tmp_path: Path):
    async def body(page):
        gate = _gate(tmp_path)
        await page.click("dds-dropdown[formcontrolname=usage] input >> nth=0")
        list_id = await page.evaluate("() => document.querySelector('dds-dropdown[formcontrolname=usage] [role=listbox]').id")
        loc = page.locator(f"#{list_id} [role=option][aria-posinset='3']")  # Mapping
        resolution = await gate.resolve(page=page, locator=loc, action="click", selector=f"#{list_id} [role=option][aria-posinset='3']",
                                        label="HIP Portal multi-select option Mapping", phase="source_document_type")
        await loc.click()
        await page.wait_for_timeout(150)
        old = await gate.verify_and_learn(page=page, resolution=resolution, action="click", phase="source_document_type")
        new = await gate.verify_and_learn(page=page, resolution=resolution, action="click", phase="source_document_type", locator=loc)
        chosen = await page.evaluate(f"() => Array.from(document.querySelectorAll('#{list_id} [aria-selected=true]')).map(o => o.textContent)")
        return resolution, old, new, chosen

    resolution, old, new, chosen = asyncio.run(_with_page(body))
    assert resolution["pass"] is True
    assert chosen == ["Mapping"]  # the click worked (the popup stays open)
    assert old["pass"] is False  # compared the first look-alike, "Flow Identifier Expression": no change seen
    assert new["pass"] is True and "target_selected_changed" in new["evidence"]


# ------------------------------------------------------------ whole phases, live gate
def test_document_type_attribute_rows_are_filled_in_one_cycle_with_the_live_gate_on(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="source_document_type", fixture="document_type_full_dds.html",
        broker=True, live_plus=True, observers=True, gate=True, max_cycles=1,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert result["gate_stats"]["target_drift"] == 0 and result["gate_stats"]["effect_not_proven"] == 0
    assert result["gate_stats"]["pre_checks"] > 50  # every action really passed the gate
    assert result["live_plus_stats"] == {"plus_clicks": 4, "rows_removed": 0}
    derived = [d["value"] for d in dom if d.get("key") == "Derived From"]
    assert derived == ["Transaction Root Element", "Element In Payload", "Element In Payload",
                       "Transaction Root Element", "Element In Payload", "Element In Payload"]  # identifier + 5 rows
    usage = [d["chips"] for d in dom if d.get("key") == "Usage"]
    assert usage == [["Flow Identifier Expression", "Logging", "Mapping", "Routing"]] * 5


def test_rule_condition_rows_are_filled_with_the_live_gate_on(tmp_path: Path):
    result, final, dom = run_phase_replica(
        tmp_path, phase="rule", fixture="rule_full_dds.html", broker=True, live_plus=True, observers=True, gate=True, max_cycles=1,
    )
    assert result["status"] == "pass", failed_fields(final)
    assert result["gate_stats"]["target_drift"] == 0 and result["gate_stats"]["effect_not_proven"] == 0
    assert [d["value"] for d in dom if d.get("key") == "Value"] == ["uhaul", "DELL"]
