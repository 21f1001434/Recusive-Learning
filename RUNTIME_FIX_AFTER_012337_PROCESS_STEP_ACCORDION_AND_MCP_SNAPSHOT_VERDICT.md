# Runtime Fix After 012337 — Process Step Accordion + Add Verification

## Verdict
The latest 012337 evidence proves the portal data was being extracted, but the executor was not validating the *effect* of a click. It clicked the Process Steps `+ Add`, which created a collapsed DDS accordion row, but because the row body was collapsed the visible-control scanner still saw zero row controls. The next candidate was incorrectly `Add Target`, which switched to a new target tab and made the agent fill the wrong active panel.

This patch fixes the root cause:

1. **Process Step `+ Add` semantic verification**
   - A `+ Add` click is not accepted just because the click succeeds.
   - For Configure Target → Process Steps, the click is accepted only when the count of `app-process-step dds-accordion-item` rows increases.
   - `Add Target` / `dds-tabs-label` candidates are rejected for Process Step row creation.

2. **Collapsed DDS accordion support**
   - Process Step rows are now counted even when collapsed.
   - Before filling row fields, the exact accordion row is expanded.
   - Fields are then filled inside that exact row, not from parent Target fields.

3. **Component-aware row filling**
   - Mapping Transformer and Enricher rows are filled by row index inside the Process Step accordion.
   - Step Type is filled first.
   - The row is re-scanned after Step Type because Mapping/Rule/Target Document fields render only after the parent value.

4. **Screenshot recovery fixed for Windows paths on Linux/CI**
   - The verifier now extracts basenames from Windows paths using `PureWindowsPath`.
   - This fixes TP screenshot false negatives when summary JSON contains `C:\...\file.png` but the zip is verified on a POSIX path.

5. **MCP capability handling clarified**
   - The existing Chrome DevTools MCP already exposes useful tools: `take_snapshot`, `evaluate_script`, `wait_for`, `fill_form`, `click`, `fill`, `take_screenshot`.
   - The missing piece was not another MCP server; it was effect validation and component-aware replay.
   - This patch uses the same MCP/CDP style model: observe DOM/component state, click, re-observe, and only trust the action if the expected child state appears.

## Validation

```text
pytest -q
204 passed in 8.04s
```

## Expected result on next run
- Configure Target → Process Steps should create Mapping Transformer and Enricher accordion rows.
- The agent should expand each row and fill Step Type, Step Name, Action, Target Document Type, Rule/Mapping fields.
- It should no longer switch into an accidental Add Target tab while trying to add Process Steps.
- Source/Target TP screenshot paths should be recovered into the aggregate report instead of showing screenshots=0 when PNGs exist.
