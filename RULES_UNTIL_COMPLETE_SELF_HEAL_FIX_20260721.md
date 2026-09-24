# Rules-only Until-Complete Self-Heal Fix — 2026-07-21

## Live failure analyzed

Run `UHAUL-POASN-FULL-DUMMY-20260721-001905` proved that Data Map and both Document Type phases completed. Rules stopped before Conditions because the required `Mapping Identifier Name (Version)` control was not found during the short rebound window.

The captured failure surface showed that the control did appear later, with the 306-item Mapping Identifier list mounted and a blocking DDS loading overlay active. The exact Rule Name validation also appeared asynchronously after the earlier duplicate check had already concluded.

## Rules runtime corrections

1. **Conditional Mapping control wait**
   - First tries the normal label-based driver.
   - If Angular has not mounted the control, waits up to 60 seconds within the current attempt.
   - Uses only the `Actions` row control `dds-dropdown[formcontrolname="target"]` with the exact label `Mapping Identifier Name (Version)`.
   - Never searches background Rules inventory or filter checkboxes.

2. **Owned asynchronous list selection**
   - Follows the Mapping combobox `aria-controls`/`aria-owns` listbox.
   - Handles 300+ options and off-screen exact options.
   - Waits while the local list or global DDS loading overlay is active.
   - Rebinds and verifies the Angular control after option selection.

3. **Late duplicate Rule Name detection**
   - Waits for the asynchronous Rule-name validator to settle.
   - If `Rule Name already exists` appears, uses a unique unsaved structural probe name.
   - Restores the exact input Rule Name after Conditions rows are created.

4. **Rules-only mode**
   - New `--rules-only` switch skips already-proven Data Map and Document Type phases.
   - Equivalent to `--phases rule`, but explicit for live repair runs.

5. **Until-complete exploration/exploitation**
   - New `--runtime-self-heal-until-complete` switch removes phase-attempt, total-repair and repeated-signature limits.
   - Safe recovery actions alternate between exploration and exploitation.
   - Every failure captures MCP, DOM, network and screenshot evidence.
   - Dell AIA text advice receives structured golden-image vision feedback.
   - The loop continues until deterministic verification, text judge and vision judge all pass.
   - Save/Create/Submit/Delete/Deploy and other final mutations remain prohibited.

6. **Golden-state feedback**
   - Each failed Rules state is compared with the approved `Rules.png` reference.
   - Vision feedback is diagnostic and guides the next safe attempt.
   - Exact DOM proof and the final independent judges remain authoritative.

## Validation

- Python compilation: passed
- Focused Rules and self-heal tests: 31 passed
- Complete source-tree suite: 443 passed
- CLI help exposes both new switches
- Existing `data/hip_memory/portal_brain` placeholders preserved

Authenticated Dell HIP execution is still required on the user's machine to prove the live portal run.
