# All-Phase Unsaved Form and Stale Overlay Recovery Fix

## Supplied run
`UHAUL-POASN-FULL-DUMMY-20260718-162957`

## Root cause
The Data Map Create form opened and the first three fill checkpoints completed. A DDS loading overlay remained for 120 seconds. The loading watchdog reloaded the unchanged route URL. In HIP, Create forms are drawers/wizards on the listing route, so reload closed the unsaved form and returned to the listing. The state-graph verifier then saw listing controls such as Table Search, Items per page and Page, and correctly rejected them as ambiguous.

## Fix
1. Detect active unsaved Create/Wizard surfaces across HIP form families.
2. Never refresh an active unsaved form.
3. When a known stale DDS loading overlay blocks an already-visible enabled target, neutralize only the presentation overlay; never assign business-control values.
4. Re-run hit-testing after overlay neutralization.
5. If recovery is unsafe or unsuccessful, stop with `HIP_PORTAL_STALE_OVERLAY_UNRECOVERED_FORM_PRESERVED` while preserving entered values.
6. Before all-phase state-graph binding, verify the expected active form surface or exact semantic control overlap. Listing/search controls can never be bound to a Create-state graph.

## Safety
The recovery does not click Save/Create/Submit/Delete/Deploy/Publish/Update/Confirm and does not alter business values. It changes only stale DDS loading overlay presentation properties after strict preconditions pass.

## Validation
- Full suite: 403 passed.
- Loading watchdog regression: passed.
- Unsaved form no-refresh regression: passed.
- Stale overlay recovery regression: passed.
- Listing-surface binding rejection regression: passed.
