# HIP Portal Document Type Active-Surface Runtime Fix

## Run analysed

`UHAUL-POASN-FULL-DUMMY-20260716-152803`

## What actually happened

Data Map passed and was promoted as validated knowledge. The run then opened the Source Document Type Create form successfully. Before field filling, the form was lost and the browser returned to the Document Types listing with the Filters drawer visible.

The old runtime still held the first form's dynamic DDS IDs and attempted 23 fills against those stale selectors. All 23 fills returned false. The final screenshot therefore showed the listing/filter surface, not the Create Document Type form.

## Confirmed root causes

1. Document Type dropdown discovery closed popups using `page.mouse.click(5, 5)`. On the HIP shell this can click the drawer/page chrome and close the Create form.
2. Repeatable-row discovery considered `div` and `span` nodes. It selected the whole listing container `div.bgColor` as the Attributes `+ Add` button.
3. The four planned Attribute-row clicks all failed and created zero rows.
4. Exploration retained dynamic selectors such as `input#dds-form-field-753019415` after Angular rerender.
5. Exploration searched the whole input profile. `Transaction Type` was incorrectly assigned `Route Document` from Rule/BizFlow instead of `856` from Source Document Type.
6. Seventy-two branch restore failures were tolerated because restore failure was non-fatal.
7. Final artifact extraction treated background filter checkboxes with `value=false` as field evidence.
8. AutoGen/httpx clients were not always closed in the event loop that created them.

## Runtime corrections

### Strict active-surface contract

The Create Document Type surface is accepted only when all of these are visible in the same foreground root:

- `Create Document Type`
- `Document Type Details`
- `Document Identifier`
- `Attributes To Configure`
- Name, Transaction Type, Version and Data Format Type controls
- Cancel and Submit footer controls

A listing, Filters drawer, Manage Columns drawer, or background table is rejected.

The surface is checked:

- immediately after `+ Add`;
- before repeatable rows;
- before and after exploration;
- before every fill;
- before final DOM/screenshot evidence;
- inside the artifact judge.

When the surface is lost, the agent safely returns to the listing, reopens `+ Add`, recreates exact rows and reacquires current controls. It never uses stale dynamic DDS IDs as durable locators.

### Safe dropdown closing

`page.mouse.click(5, 5)` was removed from Add-form dropdown discovery. DDS overlays now close using blur/focusout without clicking the page shell.

### Exact repeatable rows

Only these actionable elements may be an Add candidate:

- `button`
- `a`
- `[role="button"]`
- `dds-button`
- `dds-link`

Containers such as `div.bgColor` are impossible candidates. Each click must change the measured row count by exactly `+1`. The final row count must equal the array length from the current `input.json`.

### Dynamic value support

All Document Type values are resolved only from the current phase object, for example:

`objects.source_document_type.transaction_type`

The agent no longer searches Rule, Transport Profile or BizFlow for a similarly named key. Newly rerendered controls are located by semantic label, role, stable name and repeated-row occurrence.

### Exploration and KB learning

- Only structural Document Type parents are explored.
- Real options must be tied to the exact control.
- Restore failure is fail-closed.
- Failed exploration becomes negative evidence and cannot overlay the current plan.
- Successful live observations are applied to the same run's in-memory plan.
- Persistent canonical KB promotion still follows the configured confirmation thresholds.

### Judge correction

The final judge now rejects a Document Type phase when the exact Create surface is absent. Unchecked background checkbox values are ignored.

## Local verification

- Python compilation: passed
- Automated tests: 266 passed
- Uploaded run replay: correctly reclassified Source Document Type as failed due `filters_or_listing_surface`
- No authenticated HIP rerun was performed in this environment
