# V243R14 — Document Type fills the whole form and repairs itself (2026-09-24)

## Symptom

On **Source Document Type** the agent filled only the top *Document Type Details* section: Name, Transaction Type, Data Format Type and Description. The attribute names were visible only because the portal pre-fills them. Then it asked for human feedback.

These fields stayed empty, although the live view highlighted all of them:

- Operation;
- the identifier's Derived From and Value;
- every attribute's Derived From, Usage and Expression/Value;
- Validation Type.

Retrying repeated the same failure, so the agent never recovered on its own.

## Reproduction

`tests/fixtures/document_type_full_dds.html` is a full-length replica of the Create Document Type form. It is built from the live markup recorded in `RUN_152803_REPLAY_WITH_FIX.json`:

- DDS `dds-dropdown` inputs with `role=combobox` and `aria-controls` pointing to their own popup listbox;
- sections that render only after Data Format Type is chosen;
- five pre-filled attribute rows;
- a Usage multi-select with **Select all** and tags, and no Usage label on later rows;
- the identifier Value and the attribute Expression/Value appear only after their Derived From is chosen;
- smooth scrolling, a fixed header and a sticky Cancel/Submit bar;
- a 1280×720 viewport, so everything below the top section starts below the fold.

Running the real `execute_autonomous_phase_goal` + `execute_document_type_state_graph` against it with `examples/uhaul_poasn_full_dummy_input.json` gave exactly the live result: the top section was filled and everything else was empty after both adaptive cycles.

## Root causes

| # | Cause | Effect |
|---|-------|--------|
| 1 | After a parent field committed, the child-visibility gate waited for **every** node that listed it in `depends_on`. The dependency contract adds ordering-only edges (`section_sequence_gate`, `repeatable_row_sequence_gate`) and the family validity gates, so Name and Data Format Type "had" the identifier Value and every attribute Expression as children. Those fields appear only after their own Derived From is chosen. | The gate failed on **Name** and **Data Format Type** with `HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT`, although both values were committed correctly. |
| 2 | A failed node was cascaded through the ordering-only edges as well. | Every later field was skipped as `dependency failed`: Operation, the whole identifier, all attributes and Validation Type. Each cycle repeated the same failure, so the no-progress guard handed over to a human. |
| 3 | Before acting, the click-target check (`elementFromPoint`) ran on controls that were not scrolled into view. This affected both `_prepare_phase_control_for_action` and the BrowserSession `universal_locator_preflight`. | A control below the fold was reported as `target center intercepted`, and BrowserSession refused the click with `HIP_UNIVERSAL_FORM_POLICY_BLOCKED`. |
| 4 | In `BrowserSession.fill_and_log`, the exception handler read `autowebglm_decision` and `autowebglm_reward_recorded` before they were assigned. | Any early fill failure surfaced as `UnboundLocalError` instead of its real reason. |

## Fixes

- **`form_interaction_policy.eligible_child_nodes`.** A parent commit now waits only for children it actually reveals or enables, and only for those whose other structural parents are already committed. A grandchild is checked when its own parent commits. For example, an attribute Expression is checked when that row's Derived From commits.
- **New `structural_dependencies()` / `ordering_only_dependencies()`.** Both executors (`execute_document_type_state_graph` and `execute_phase_state_graph`) now skip a field only when a structural parent failed.
  - If only an earlier section or row failed, the field is still attempted, so the rest of the form is filled.
  - These fields wait at most 1.5 s to appear.
  - The run records them in `ordering_predecessor_failures`.
  - The phase still fails closed while any field is wrong.
  - The cycle's full-graph repair pass, or the next adaptive cycle, repairs only the failed field; fields already committed are left alone.
- **New `scroll_control_into_view()` / `scroll_locator_into_view()`.** The control is centred with `behavior: 'instant'`, which overrides the portal's smooth scrolling, before the stability and click-target checks. This applies to both executors and to the BrowserSession preflight for clicks and fills. If a sticky bar still covers the control, it is re-centred once.
- `BrowserSession.fill_and_log` initialises its model-decision variables up front.

This applies to Source and Target Document Type and to the generic executor used by Rule, Transport Profile and BizFlow.

## Evidence

| Scenario | Before | After |
|---|---|---|
| Full replica, real input (29 fields), offline executor path | Top section only; `failed_closed` after 2 cycles | **Pass on cycle 1.** All 29 fields exact, including 5 × Usage with 4 values each and 4 Expressions. Transaction Type correctly has no Expression. |
| Same replica through the real `BrowserSession` click/fill broker (semantic MCP gate disabled because the MCP servers are not running in tests) | — | **Pass on cycle 1**, all 29 fields exact. |
| Same replica through the broker with the new scroll step disabled (control run) | — | Fails. Attribute rows below the fold are reported as `required semantic control unresolved`, which shows the scroll fix is required. |
| Operation options load late (slow portal lookup) | Under the old skip rule, Validation Type and the attributes were skipped once Operation failed, so the options never loaded and a human was needed. This follows from the code; it was not run. | The fill pass fills every attribute and Validation Type; only Operation's own identifier row is skipped. **The same cycle's full-graph repair pass then fixes Operation, Derived From and Value by itself.** No human is needed. |

Tests: `tests/test_v243r14_document_type_full_form.py` covers:

- child-gate semantics;
- structural vs ordering-only dependencies;
- the full form on one cycle;
- the BrowserSession broker path;
- self-repair of the late dropdown;
- the preflight scrolling a below-the-fold target into view.

## Verification

| Check | Result |
|---|---|
| `tests/test_v243r14_document_type_full_form.py` | 6 passed |
| Full suite, other 176 files | 1,304 passed, 1 skipped. The only failure is `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs `uploads/Transform_DELLCoXMLASNXX08C.jar`. That file is gitignored in the repository checkout and present in the package. `test_v210_layer1_windows_path_guard.py` runs only on Windows. |
| 7-phase local mission UAT (`certify-final-mission`) | `pass: true`; all 7 phases pass |
| `VERIFY_V243R14_INSTALL.ps1` R14 smoke checks | All pass |

## Apply

```powershell
.\APPLY_V243R14_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R14_INSTALL.ps1
```

Restart the Control Center, then start a new mission.

Validation boundary: verified on a browser replica built from the recorded live markup. It was not verified against the live Dell HIP tenant. If a live field still fails, the Control Center error names that field and its reason, and the other fields are still filled.
