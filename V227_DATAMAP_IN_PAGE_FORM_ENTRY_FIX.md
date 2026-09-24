# HIP Portal Agent v2.2.7 — Data Maps In-Page Form Entry Fix

Date: 2026-09-09

## Live issue

HIP Data Maps does not navigate to a separate Create Map URL. The page-level top-right `+ Add` opens an in-page drawer/form on the existing Data Maps route. Earlier code could click a generic Add/link before the ReAct entry controller and could misclassify the progressive initial drawer as not open.

## Implemented fix

- Data Maps has a single ReAct-owned Add transaction.
- Exact Add candidates are ranked by page-level/top-right position.
- Add controls inside rows, menus, forms, dialogs, and drawers are rejected.
- Anchor candidates are rejected when their href changes the Data Maps route.
- `ensure_phase_form_entry(..., require_same_route=True)` rejects any path change after Add and restores the canonical Data Maps listing before retry.
- Query/hash-only changes are allowed because HIP can encode drawer state without leaving the listing route.
- Initial form proof requires the in-page `Create Map` surface plus core controls; later/lazy upload fields are not required merely to prove that the drawer opened.
- The downstream strict state-graph executor still requires semantic binding, fill/select/upload effect verification, and exact completion before section judging.

## Expected runtime flow

`Authenticate -> Data Maps listing -> top-right + Add -> same-route Create Map drawer -> control discovery -> semantic bind -> fill/verify -> exact execution -> section judge`

A path change such as `/datamaps/create` is treated as a wrong Add target, not as success.
