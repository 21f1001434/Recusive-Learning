# V243R15 — Every phase fills its whole form, and the task box uses the learned phase knowledge (2026-09-24)

R14 fixed Document Type. The same engine runs Data Map, Rule, Transport Profile and BizFlow. R15 proves and hardens those phases the same way, and makes the Control Center task box use them.

## How it was tested

There is now a full-length browser replica of every create form in `golden_screenshots/UHAUL-POASN`:

- `tests/fixtures/data_map_full_dds.html`
- `tests/fixtures/rule_full_dds.html`
- `tests/fixtures/transport_profile_full_dds.html`, used for both Source and Target
- `tests/fixtures/bizflow_wizard_dds.html`, which has four tabs, a process-step accordion and the Configure Routing rule drawer

They share `tests/fixtures/hip_dds_kit.js`, which reproduces the Dell DDS widgets and behaviour:

- `dds-dropdown` with an owned popup listbox;
- multi-select chips;
- switches, checkboxes and radio groups;
- hidden file inputs;
- conditional fields;
- a fixed header, smooth scrolling and a 720 px viewport.

Each replica is run through the real `execute_autonomous_phase_goal`, both directly and through the real `BrowserSession` click/fill broker.

## What failed, and why

| Phase | Symptom on the replica | Root cause | Fix |
|---|---|---|---|
| Transport Profile | System Name was filled into **System Type**. | A node owned by the whole create surface gave 55 points to controls directly under the page heading and 10 to controls inside fieldsets. | `_node_section_score` scores every section of a generic create surface alike. |
| Transport Profile | "Unintended mutation" after opening System Type. | The option flags (`aria-selected`) re-render when a dropdown opens, although the value is unchanged. | `_protected_state_changes` compares committed values only (`_committed_semantics`). |
| Transport Profile | One failure skipped all 13 later fields. | The compiler chained every field to the previous one. | Profile Name → Usage → Interface Type are now `field_sequence_gate` (ordering only). The real parents stay structural: System Type → System Name and Usage → Deployment Group. |
| Transport Profile | Existing Account / Use Existing Folder tied with each other and with a checkbox. The driver clicked the first "No" in the section. | A radio's label is its option (Yes/No). The group label ("Existing Account") was never captured, and `select_radio_value` ignored the group. | The scanner records `group_label` / `group_name`, and scoring uses them. The new `select_radio_option` clicks the matching option inside the bound radio's own group. |
| Transport Profile, BizFlow | `...IB (1.0)` in the portal did not match `...IB(1.0)` in input.json. | The single-select verifier compared text literally, although the driver chooses options by semantic key. | Single-select values are verified by semantic key. |
| BizFlow (every tab) | Filled tabs still failed "form model not one-to-one". | The final model was built over all 58 BizFlow nodes while only one tab is visible. | A section run is modelled on that section's nodes only. |
| BizFlow Flow Details | `current_flow_version` could never bind. | The portal shows it only as text: "Current Flow version : 1.0". | Portal display proof (`_display_only_evidence`). A version or number value visible next to its label, or a row ordinal equal to its position, is accepted. Editable fields never are. |
| BizFlow Configure Source | Source "Document Type Name" tied with the Flow Identifier row's "Document Type Name (Version)". | A field outside any repeatable row could bind inside one, and the unlabelled second row had no row kind. | A field outside any row is penalised for binding inside a row. Unlabelled rows inherit their siblings' kind. |
| BizFlow Configure Source / Target | "Unintended mutation" on row fields. | The row binder upgrades a row's identity from its position to a semantic anchor, which was read as a change. | Identity is ignored in the committed-value comparison. |
| BizFlow Configure Target(s) | Step 2 (collapsed, as in golden CT-1) was never opened. | `reveal_hidden_structural_parent` took the visible Step 1 "Step Type" as its target. | The reveal uses the row: row N is the N-th matching control. |
| BizFlow Configure Target(s) | File Name parts were counted as the Process Step row. | Row detection used the first matching selector, not the innermost container. | The innermost row container wins, and a class-based kind hint is used. |
| BizFlow Configure Target(s) | The **Date And Time** part's Value (a dropdown in the portal) passed while only typed. | input.json says `fill_text`. Typing fills only the dropdown's search box, and the text is lost on blur. | `_effective_action` picks the driver from the live control: `fill_text` on a DDS combobox selects the option, and `select_single` on a textbox types. Recorded as `adapted_action`. |
| BizFlow Configure Target(s) | Step 1's disabled Source Document Type "disappeared". | When Step 2 appeared, both disabled "Default (All/Other)" fields tied on re-resolution. | The after-snapshot falls back to the same physical control. |
| All | A field below the visible area could lose its binding to a similar on-screen field. | The scanners marked off-screen controls non-interactable, costing 47 points against on-screen controls. | Controls outside the viewport are no longer penalised; they are scrolled into view before any action (R14). |

Also: a reconciled attempt keeps `initial_failure_reason`, so the original cause stays visible.

## Task box: complex tasks use the learned phase knowledge

`fill_from_input` used to bind input.json leaves to controls one at a time, with none of the phase knowledge:

- dependency order;
- radio groups;
- repeatable-row kinds;
- conditional reveals;
- the Document Type executor.

Now `resolve_hip_phase_for_task` recognises a HIP object from any of:

- the input root (`$.objects.rule`);
- the task wording ("create the target transport profile", "create biz flow");
- the open form's heading ("Create Rule").

The task box then runs that phase's compiled graph through the same autonomous goal as a mission, with the same self-repair cycles. For BizFlow it walks the wizard tabs and opens the Configure Routing drawer. Unknown forms keep the generic binder.

A Transport Profile task is ambiguous when it says neither "source" nor "target". It then also keeps the generic binder instead of guessing.

On the Transport Profile replica, the same task ("Create the source transport profile from input.json") gave:

| Path | Result |
|---|---|
| Generic binder (before) | `needs_human_assistance`: 5 fields unresolved (System Name, both Yes/No groups, Existing Account Name, Subscription Folder) |
| Phase-aware (R15) | `100_percent_runtime_input_exact_readback`, all 15 fields exact, radios in the correct groups |

"Create biz flow U-HAUL from input.json" on the wizard replica:

- the task box clicked all four tabs itself;
- it opened the Configure Routing drawer with "+ Add";
- every section was proven on the first cycle (`100_percent_runtime_input_exact_readback`).

A successful phase-aware task returns a value-free skill blueprint. The task executor already feeds it to deterministic recipes and skill induction, so later tasks replay faster.

## Evidence

Every run below is `goal_achieved` on the first adaptive cycle.

| Replica | Offline driver | Real BrowserSession broker |
|---|---|---|
| Data Map (7 fields + JAR upload) | pass | pass |
| Source Document Type (29 fields, R14) | pass | pass |
| Rule (19 fields, including the unlabelled condition row) | pass | pass |
| Source Transport Profile (15 fields) | pass | pass |
| Target Transport Profile (15 fields) | pass | pass |
| BizFlow Flow Details | pass | pass |
| BizFlow Configure Source (13 fields) | pass | pass |
| BizFlow Configure Target(s) (24 fields, collapsed Step 2, Date And Time dropdown) | pass | pass |
| BizFlow Configure Routing (20 fields in the drawer) | pass | pass |

New tests:

- `tests/test_v243r15_engine_units.py`
- `tests/test_v243r15_datamap_rule_replicas.py`
- `tests/test_v243r15_transport_profile_replicas.py`
- `tests/test_v243r15_bizflow_source_replica.py`
- `tests/test_v243r15_bizflow_target_routing_replica.py`
- `tests/test_v243r15_task_box_phase_aware.py`
- `tests/test_v243r15_task_box_bizflow.py`

## Verification

| Check | Result |
|---|---|
| Full suite (183 files, 600 s per file) | 1,335 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` (needs the gitignored `uploads/*.jar`, present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| `tests/test_v243r15_task_box_bizflow.py` (added after the suite started) | 1 passed |
| 7-phase local mission UAT (`certify-final-mission`) | `pass: true`; all 7 phases pass |
| `VERIFY_V243R15_INSTALL.ps1` R15 smoke checks | All pass |

## Apply

```powershell
.\APPLY_V243R15_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R15_INSTALL.ps1
```

Restart the Control Center, then start a new mission or task. R15 includes R13 and R14.

## Validation boundary

The replicas are rebuilt from the golden screenshots and the markup recorded in earlier runs. They are not the live Dell tenant. The following were inferred from the screenshots and the existing code, and still have to be proven live:

- how the portal marks up radio groups;
- the routing drawer's position in the DOM;
- the accordion's open/close behaviour;
- real option lists.

If a live field still fails, the error names that field and the check. The rest of the form is still filled, and the repair pass retries only that field.
