# V243R20: every input.json row is created with the portal's own "+" and filled (2026-09-25)

## What was reported

The agent highlights the right fields on every phase but does not fill them, or leaves sections short. This happens wherever the portal needs a "+" click to add rows before input.json fits:
- Document Type attributes and document-identifier rows;
- Rule conditions;
- BizFlow flow identifiers, process steps, file-name parts and routing conditions.

## What the live portal really renders

From the live runs recorded in `rules_kb.py`, and the golden screenshots:

- The "+" is an **icon-only `dds-button` inside the list's `<legend>`**: "Attributes to Configure ⊕", "Document Identifier ⊕", "Conditions : ⊕", "Actions : ⊕", "Attributes: ⊕", "Process Steps : ⊕", "File Name Section : ⊕".
- Its only visible content is `span.dds__icon--add-cir`. Its name exists only in a **hover tooltip** such as "Create Condition".
- Every row has a ⊖ remove icon.
- Lists start with **one row**, or with none ("No Process Steps Added").
- Rows after the first carry **no labels**.
- A click that lands while a dropdown popup is still open is **swallowed**: it only finishes that dropdown.

None of the replicas modelled this. They pre-rendered all rows (BizFlow had 2 identifiers, 2 steps, 2 file parts and 2 routing conditions; Document Type had 5 attributes). Their variants used friendly "+ Add Condition" text buttons, so the tests passed while the live forms stayed short.

## Reproduced

The kit now has a live mode (`window.__livePlus`: `liveList`, `plusButton`, `minusButton`) that renders exactly the markup above. The Document Type, Rule and BizFlow replicas use it.

Before this release, the Rule form filled condition row 1 and then reported `no_add_control_found`: it needed 2 rows, had 1, and made 0 "+" clicks. It failed after 3 cycles (326 s). The other lists failed the same way. Where a "+" was clicked (BizFlow Source attributes, Routing conditions), the new row was not recognised, so the phase ended `needs_input`.

## Root causes and fixes

| # | Root cause | Fix |
|---|---|---|
| 1 | The row adder recognised only `plus` or `add-circle` icons and "+ Add" text, and **rejected any label containing "create"**. The live plus (`add-cir`, tooltip "Create Condition") was never a candidate. | Recognises icon-only add controls by icon class (`add-cir`, `add`, `plus`…), by tooltip, `title`, `aria-label` or `aria-describedby`, and by the legend/heading they sit in. Visible "Create", "Save" or "Submit" buttons and ⊖ remove icons are never candidates. A `dds-button` host and its inner button count as one control. |
| 2 | **With no row on screen** (process steps), an icon-only plus was never allowed. | Allowed when its own legend or heading names the list ("Process Steps :" for `process_steps`). Otherwise nothing is clicked. |
| 3 | A plus nested inside an existing row (File Name Section inside process step 2) could win over the list's own plus, because it was nearer. | Candidates that name the list come first. A plus inside an existing row is used only when it names the list. |
| 4 | **Rows added by "+" have no labels**, so the capture classified them by the FormArray name ("attributes") or by unrelated nearby text ("routing_action"), not as their list ("flow_identifier", "routing_condition"). The new row was not counted, the click looked ineffective, and the row stayed empty. | Every row of an Angular FormArray takes the kind read from its first, labelled row. Rule/BizFlow `conditions` rows now carry their FormArray hint too. |
| 5 | An open dropdown popup could cover the plus or swallow its click, and an ineffective click was never retried. | Open popups are settled (non-clicking blur) before every plus click. A click that added no row is retried once. |
| 6 | The broker labels "Create Condition row" (Rule) and "... row: Create Attribute" (BizFlow) read as final mutations to the safety guards. | A row plus is dispatched as a structural opener ("structural_opener add row Conditions"). The Python guard accepts a structural opener only on an element with an add icon; the in-page guard treats it as one-shot. |

The same generic row adder serves every phase, both at the start of each cycle and just before a field of row N is filled. The phase-specific Rule and BizFlow "+" helpers run first and benefit from fixes 4 and 6.

## Verified

On the live-faithful replicas (real broker, live DOM observers), each list is created with its own "+", filled exactly, and no row is removed:

| Phase / tab | Lists | "+" clicks | Result |
|---|---|---|---|
| Document Type | 2 identifier rows (1 on screen), 5 attribute rows (1 on screen) | 5 | pass, every value exact |
| Rule | 2 conditions (1 on screen); the Actions "+" must not be used | 1 | pass |
| BizFlow Configure Source | 2 flow-identifier rows (1 on screen) | 1 | pass |
| BizFlow Configure Target(s) | 2 process steps (none on screen); 2 file-name parts inside step 2 (1 on screen) | 3 | pass |
| BizFlow Configure Routing | 2 routing conditions (1 on screen) | 1 | pass |

Data Map and Transport Profile have no repeatable lists in the U-HAUL input. The R17 variants ("+ Add Tag", Cross Reference rows) still pass.

## Tests

`tests/test_v243r20_live_plus_rows.py` (10 tests):
- the five phase cases above;
- recognition: the legend plus is found by its tooltip, and the ⊖ icon and a visible "Create" button never are;
- an empty list grows only through the plus in its own legend;
- an open dropdown is settled before the click;
- a swallowed click is retried once;
- the broker label never reads as a mutation.

## Verification

| Check | Result |
|---|---|
| Full suite (194 files) | 1,409 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| R20 tests | 10 passed |
| Live-"+" replicas, default U-HAUL input | Rule 1 click, BizFlow Source 1, Target(s) 3, Routing 1, Document Type 4 (4 attribute rows added, 1 identifier row needed): all pass, all values exact, no row removed |
| Learn, then certified replay, on live-"+" forms | Rule: learned (candidate), then replayed deterministically on a fresh one-row form with 1 "+" click and certified. BizFlow Target(s): learned, then replayed from "No Process Steps Added" with 3 "+" clicks and certified. Every value exact. |
| R15 / R17 replica regressions | Pass (pre-rendered rows, "+ Add" text buttons, Tags, Cross Reference rows) |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R20_INSTALL.ps1` R20 smoke checks | `R20_LEGEND_PLUS_RECOGNISED_OK`, `R20_ADDED_ROWS_CLASSIFIED_AND_CLICKABLE_OK` (and the R19 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R20_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R20_INSTALL.ps1
```

R20 includes R13–R19. config.yaml needs no change, and `data\hip_memory` is preserved.

## Validation boundary

The "+" markup is taken from the live runs recorded in the code, and from the golden screenshots.

If a live list's "+" is named differently from its input key and its legend, and no row is on screen, the agent does not guess; that list is reported, not filled. The row-heal evidence for that case is in the phase's `autonomous_form_runtime.json` (`structure_heal.rows.groups`).
