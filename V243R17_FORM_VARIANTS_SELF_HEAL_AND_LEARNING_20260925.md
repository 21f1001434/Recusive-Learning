# V243R17: Radio groups, extra sections and "+ Add" rows are filled in every phase, and the agent learns each form's structure (2026-09-25)

## What was reported

In each phase a form can hold more than the golden U-HAUL layout:
- radio groups;
- several sections, some collapsed;
- rows that have to be added;
- fields that appear only after another choice.

The agent did not fill these, did not repair them itself, and did not learn them.

## How it was tested

Every phase's browser replica gained a variant mode (`window.__variant`) with the structures a DDS form can have. input.json gained keys the phase compilers do not know.

| Phase | What the variant adds |
|---|---|
| Document Type | Data Format **EDIX12** reveals an "EDI Delimiters" section: 3 separators and a Yes/No radio group. The form starts with 1 identifier row (icon-only "+") and 1 attribute row ("+ Add Attribute"); input needs 2 and 3. |
| Data Map | The "Cross Reference Table details" switch reveals a row list ("+ Add Row", 2 rows needed). A **collapsed** "Advanced Options" section holds a radio group whose real input is clipped (DDS visually hidden) and a checkbox group. |
| Rule | Conditions start with 1 row ("+ Add Condition", 3 needed). "Execute Always" switch. A collapsed "Advanced" section with a **button-style** radio group (role=radio, no input). |
| Transport Profile | Existing Account **No** reveals "Account Name"; Use Existing Folder **Yes** reveals "Existing Folder". A Tags row list ("+ Add Tag"). A collapsed "Notification Settings" section with a checkbox group, a button radio group and a text field. |
| BizFlow (Flow Details) | A hidden-input radio group "Flow Type", and a collapsed "Alert Settings" section with a switch and a checkbox group. |

Each variant runs through the real `execute_autonomous_phase_goal`, the same code a mission uses. Transport Profile and the learning test also go through the real `BrowserSession` click/fill broker.

### Before R17

| Phase | Result |
|---|---|
| Document Type | `needs_input` after 4 cycles (303 s). Only 1 identifier row and 1 attribute row were ever present; the Acknowledgement radio was never set. |
| Data Map | `needs_input`. **Row 2 was typed over row 1** (the list showed CA/124 only). "Advanced Options" was never opened. |
| Rule | Killed after 600 s. Rows 2 and 3 were **reported filled because row 1 already held the same Condition Type**. |
| Transport Profile | `failed_closed` after 537 s. **Use Existing Folder ended on "No" although input said "Yes"**. Tag row 2 was typed over row 1. Notification fields were bound to the "No" radio. |
| BizFlow | `needs_input`. Flow Type was not chosen; the Alert Settings were never opened. |

### After R17

| Phase | Result | Cycles | Time |
|---|---|---|---|
| Document Type | pass. 2 identifier rows, 3 attribute rows, `~ * >`, Acknowledgement = Yes | 1 | 122 s |
| Data Map | pass. US/840 and CA/124 in their own rows, XSLT, exactly Failure + Warning | 1 | 47 s |
| Rule | pass. 3 condition rows, Execute Always on, Priority = High | 1 | 121 s |
| Transport Profile | pass. No → Account Name, Yes → /Outbound, 2 tag rows, Failure, Webhook, email | 1 | 108 s |
| BizFlow Flow Details | pass. Passthrough, alerts on, exactly Failure + Delay | 1 | 18 s |

Every value was read back from the page after the run.

## Root causes and fixes

| # | Root cause | Fix |
|---|---|---|
| 1 | **A radio option's own label ("Yes", "No") was treated as the field name**. The two options of one group tied with each other, so nothing was chosen. Short words matched inside unrelated keys ("No" inside `notify_on`), so notification fields bound to the Use Existing Folder "No" radio and clicked it. | Radios and checkbox groups are matched by their **group label** (the question). The options of one group form one candidate. Words shorter than 3 letters never match by containment. The input value (true, "Y", "Webhook") is mapped onto the group's own option. |
| 2 | A list value for several checkboxes ("Notify On": Failure, Warning) had no action. | A new `select_checkbox_group` binding becomes one switch node per option: the listed options are ticked, the rest are cleared. |
| 3 | Fields in a **collapsed section** are not rendered, so they could never be bound. | `form_structure_healer.reveal_collapsed_sections` opens collapsed accordion or `<details>` sections in the active form: first those whose title matches the missing input (`notification_settings` → "Notification Settings"), otherwise all of them, bounded. Dropdowns, menus, tabs and anything that could save or submit are excluded. |
| 4 | A field for **row N** could bind to row 1 when row N did not exist yet. Row 1 was overwritten, and an equal row-1 value counted as row N's success. | A control proven to be in another row is **excluded** (`ROW_EXCLUDED_SCORE`), not merely scored lower. Row evidence is the reconciled row, then the row's position within its row kind (`row_kind_ordinal`), then label occurrence. Angular `formgroupname` rows inside a `formarrayname` list are now recognised as rows. |
| 5 | The fill loop never clicked **"+ Add"**. Rows came only from each phase module's fixed row counters. | `ensure_repeatable_rows` counts the rows each list needs (from the graph and from input.json lists) against those on screen. It clicks that list's own "+ Add…" (text, "+" icon or aria-label; the nearest one to the existing rows, or one that names the list). Each click must add a row, otherwise it is not repeated. It runs at the start of each cycle, between the passes, and **inside the filler** the moment a row-N field has no row. Collapsed existing rows are opened first, so a collapsed Step 2 is never duplicated. |
| 6 | Fields that appear **after** a choice (EDI separators after the format, Account Name after "No") were learned only after the fill. The goal could be declared met before they were filled. On a BizFlow tab they were filed under their sub-section, skipped by the tab run, and the tab passed anyway. | The form is re-read between the two passes of a cycle, so revealed fields are filled in the same cycle. A cycle is not complete while fields found after the fill, or runtime fields of the scope that no pass proved, remain (`new_fields_found_after_fill`, `unexecuted_runtime_nodes`). Runtime fields belong to the run's section, with their sub-section as an alias. |
| 7 | DDS **visually hidden radios** (the real input is clipped to nothing) failed the hit test. | The scanner records the label that operates such an input (`click_proxy`). Preparation probes the label's geometry. Radios and checkboxes are clicked through the label (`_click_choice`), still through the same broker. Button-style radios read their own text as their option. |
| 8 | Punctuation-only values (`~`, `*`, `>`) have no semantic key and could never verify. | They are compared exactly. |
| 9 | The Document Type executor had no **toggle** action, and its scorer ignored group labels. "No" also matched inside "Acknowledgement". | Toggle was added to the Document Type executor and verifier. Group label and option are scored as in the other phases. Label containment needs a real word. |
| 10 | A parent that reveals a repeatable section (Data Format) failed as "child not visible", because rows 2..N do not exist until "+ Add". The failure cascaded to the whole form. | Rows after the first are not required to appear when their parent is chosen. |

## Learning: the agent keeps what it found

`hip_id_agent/form_structure_memory.py` stores, per phase, in `<memory_dir>/form_structure_memory/<phase>.json`:

- **fields**, keyed by phase-relative input path (`tags[*].key`, `notification_settings.notify_on`): action, label or group label, the portal's option labels, section and row kind;
- **sections** that had to be opened to reach input;
- **rows**: the "+ Add…" control that created a list's rows.

The next run:
- seeds those fields into the goal before it looks at the page;
- opens the learned sections first;
- prefers the learned Add control.

Every seeded field is still bound live and proven by read-back. A learned field that no longer binds is dropped for that run and demoted, and removed after repeated misses, so the live page always wins over memory.

**Only structure is stored.** No input values, selectors or coordinates are kept. A radio group keeps the portal's own option labels, not the chosen option. `memory_dir` is `reporting.memory_dir` (`data/hip_memory` by default), which the APPLY script preserves.

On the Data Map variant (two runs through the broker, one memory folder):

| | Run 1 | Run 2 |
|---|---|---|
| Fields seeded from memory | 0 | 9 (5 learned fields; the checkbox group is one node per option) |
| "Advanced Options" opened before the first scan | no | yes |
| Input keys unknown at the start | 4 | 0 |
| Learned | 5 fields, 1 section, 1 row list | re-confirmed |

## Tests

- `tests/test_v243r17_variant_forms_a.py`: Document Type EDI, Data Map, BizFlow Flow Details variants.
- `tests/test_v243r17_variant_forms_b.py`: Rule, and Transport Profile through the broker.
- `tests/test_v243r17_structure_learning.py`:
  - learn, then reuse on a second run (value-free check);
  - demotion;
  - checkbox-group seeding;
  - short-word matching;
  - radio groups as one target;
  - row exclusion;
  - row planning;
  - punctuation values;
  - option mapping;
  - the post-fill check.

Every earlier replica test (R14 Document Type, R15 all phases and task box, R16 watchdog) still passes. The one updated test is `test_all_phase_form_interaction_policy.py`: radio clicks now go through `_click_choice`, which uses the same `_broker_click`.

## Verification

| Check | Result |
|---|---|
| Full suite (188 files) | 1,362 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R17_INSTALL.ps1` R17 smoke checks | All pass |

## Apply

```powershell
.\APPLY_V243R17_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R17_INSTALL.ps1
```

Restart the Control Center and start a new mission. R17 includes R13–R16.

## Validation boundary

The variant structures are standard Dell DDS and Angular patterns rebuilt in the replicas. They were not captured from the live tenant, so the live portal's exact markup for these sections was not seen.

If a field still fails live, look in that phase's `autonomous_form_runtime.json`:
- `cycles[].structure_heal` shows which sections were opened and which "+ Add" was clicked;
- `runtime_input_leaf_ledger*.unresolved_input_leaves` shows each input key the form could not place, with the best candidate labels.

`form_structure_memory/<phase>.json` shows what was learned.
