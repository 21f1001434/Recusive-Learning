# V243R24: the model is chosen once by live task performance; Edit / Clone / Migrate go through the row's expander (2026-09-27)

## What was asked

- Model selection should happen in the live go-live test: give each model a task, see how it performs, then select that model and use it. This should happen once.
- On each section's landing page, the agent searches for the object, then clicks the row's expander. The expander shows Edit, Clone and Migrate (and Deploy where the portal has it):
  - Migrate offers the target environments;
  - Edit and Clone open the page where the changes are made, and the agent makes them there.
- The landing page also lets you choose what to edit or change, for example the environment and version of a Document Type.

The screenshots of 2026-09-27 and the portal-learning prompt show how the live Document Types listing works:
- the row has no action button; its first cell holds a chevron `button.dds__td--expandable__button` ("Expand the row", `aria-expanded`);
- the expanded details show, in order:
  - "Description: …";
  - the DEV / TEST1 / TEST2 / PROD tabs;
  - "Version :";
  - Edit / Clone / Migrate;
  - the read-only Document Type Details;
- Migrate (`aria-expanded`, `aria-controls`) opens a menu of target environments (TEST1 / TEST2 from DEV);
- Edit / Clone open "Edit Document Type" / "Clone Document Type" over the listing.

## 1. The model is chosen by live task performance, once

**Before.**
- The model was chosen by a capability table (R21) and by champion evidence collected during missions (R22).
- No model was ever compared with the others on the same live task.

**Now.** During **Live GO/NO-GO**, that is, the Windows live-runtime certification (`certify-live-runtime`, "Certify Windows runtime" in the Control Center), the agent:
1. opens the live Document Types listing (read-only: navigation and reading only, nothing is clicked);
2. reads the page's visible controls: accessible name, form label, placeholder, the table row each belongs to, and `aria-expanded`;
3. builds questions whose right answer is read from the page itself:
   - "The task is to edit / migrate / clone the object named '<row>'. Which control do you click first?" The answer is that row's expander, on three different rows.
   - "Which control finds an object by its name?" The answer is Table search.
   - "Which control opens the create form?" The answer is + Add.
   - "Which control shows the next page?"
   - On form pages: "Which control receives input.json value 'transaction_type'?"
4. gives **every available Dell AIA text model the same page and the same questions**; each answers with control ids;
5. scores each model against the page, never against another model or a model's own confidence:
   - accuracy decides;
   - latency breaks ties;
   - a model must answer at least 60% of the questions correctly to qualify;
6. selects the most accurate model and **locks** it.

From then on, **every call uses the selected model**:
- default calls (`HIP_MODEL_ROUTER_SELECTED_TEXT`, set when any process starts);
- action selection, judges and recovery, including learning and complex tasks, which used to ask several models.

If the selected model is proven down, the next model that also passed the same qualification takes over.

**It runs once.**
- Later certifications report "already qualified: <model> (n/m correct, <date>)" and ask no model anything.
- Run it again with "Re-run the model qualification" in the Control Center, `certify-live-runtime --requalify-models`, or `qualify-models --force`.
- If certification never ran, the first live mission page qualifies the models once (read-only).

| Where | What you see |
|---|---|
| Live certification table | "Model selected by live task performance (one time)" with the model and n/m correct |
| Model Champion tile | the selected model; "selected by live task: n/m correct • date" |
| `qualify-models --show` | every model's correct answers, accuracy, latency and whether it qualified |
| Evidence | `<run>/model_qualification/model_qualification.json` (questions, answers, ranking) and `model_qualification_screen.json`; the lock is `data/hip_memory/model_portfolio/model_selection.json` |

Settings (`model_portfolio`): `qualification_enabled`, `use_qualified_model`, `qualification_min_accuracy` (0.6), `qualification_max_questions` (8), `qualification_in_first_live_mission`, `qualification_page` (`source_document_type`).

## 2. Edit / Clone / Migrate through the row's expander

**Before.**
- The operation runner looked for the action on the row itself, or in a "More actions" menu.
- The live Document Types rows have neither, so the request fell through to the semantic resolver.

**Now.**

| Step | What the agent does |
|---|---|
| Search | Types the name into the listing's search box (Table search). |
| Exact row | Uses the row whose name cell is **exactly** the requested name: "Abbvie_SRC_DocType" never opens "Abbvie_SRC_DocType_IN". It stops with **NEEDS_INPUT**, listing the candidates, when several rows match and none is exact. It also stops with NEEDS_INPUT when no row has that name. |
| Expand | Clicks the row's chevron and waits for the expanded details. |
| Prove the details belong to that row | Checks that the details' Name field holds the requested name (else the details' text, else that row's own expanded sibling). Recorded as `verified_by`. |
| Choose environment and version | Clicks the environment tab the request names (DEV / TEST1 / TEST2 / PROD) and picks the Version. A tab that is not available, or a version that is not offered, stops with NEEDS_INPUT, listing what is offered. Other choices on the details (a tab or a labelled dropdown) come from `"panel": {"<label>": "<value>"}` in input.json. |
| Action | Clicks Edit / Clone / Migrate inside those details. |

**Migrate** (also "deploy … to <ENV>" on objects that have no Deploy button):
- **Before any click**, the agent reads the row's Available Environments. If the target already holds the selected version, the result is **EXISTING** and nothing is clicked. To check the version, the agent clicks the target's tab, reads its versions, then switches back to the source tab.
- It opens the Migrate menu and checks that the requested target is offered. "PROD" from DEV is not offered (TEST1, TEST2 are), so the result is NEEDS_INPUT and nothing is clicked.
- It clicks the target once. If the portal shows a confirmation ("Migrate … from DEV to TEST2?"), the agent reconciles the first click as "opened a confirmation, nothing written" and clicks the confirmation once. Both clicks go through the three-part mutation gate.
- It verifies that the listing row now shows the target environment badge.

**Edit** (edit safety):
1. Captures the open form's values first (the "before" snapshot).
2. For every requested field, records CURRENT / REQUESTED / CHANGE. Fields that already have the requested value are not touched. When nothing differs, the result is **EXISTING** (`no_change_needed`) and nothing is saved.
3. Fills only the fields that differ: the fill is learned, proved by a replay on a reopened form, then replayed.
4. Lets the form settle and snapshots it again. **If any field that was not requested changed** (for example, a portal reaction cleared Description), the result is FAILED with `unrelated_field_changed`, and **nothing is saved**.
5. Clicks Submit once through the gate, confirms the row in the listing, then **re-opens the object's details** and checks that every requested value is shown (`after.requested_values_seen`).

**Clone:**
- Needs a new, unique name. When input.json and the request give none, or give the source's own name, the result is NEEDS_INPUT and nothing is opened.
- The source configuration (transaction type, identifier, attributes, validation) is kept. Only the requested fields change.

Every operation now reports `result`: **SUCCESS / EXISTING / FAILED / BLOCKED / NEEDS_INPUT**. It also reports the before/after evidence and, for NEEDS_INPUT, which value is needed, what the portal offers and where the value can come from.

Requests understood by the task box:
- "migrate document type Abbvie_SRC_DocType_IN from DEV to TEST2";
- "migrate the document type X version 1.0 to TEST1";
- "edit the DEV version 1.0 of document type X and save";
- "clone document type X" (values from input.json);
- "deploy the document type X to TEST2".

## 3. What is learned: semantic action paths, EXPLORATION → DETERMINISTIC

**Where the path is kept.**
- A proven action path is stored per object and action, for example `expand row > tab:DEV > migrate`.
- Its selectors are semantic: role, accessible name and context, such as `{"role": "button", "name": "Expand the row", "context": "row whose name cell is exactly the requested object"}`.
- Coordinates and generated CSS paths are never stored.

**How it is promoted.**
- A path starts as **EXPLORATION**.
- After two verified outcomes (a committed and verified action, or EXISTING), it becomes **DETERMINISTIC**.
- A deterministic path that fails drops back to exploration.
- `portal-skills --verify source_document_type:migrate` marks a path human-verified, which makes it deterministic at once.
- `portal-skills` shows `action_paths` with each path's mode.

## Tests

`tests/test_v243r24_live_qualification_and_row_panel_operations.py` (19):
- **Model qualification:**
  - the questions' answers come from the page: the row questions resolve to each row's "Expand the row";
  - every model gets the same task and the most accurate one is selected;
  - the failing and erroring models are ranked;
  - the fallback order holds the models that also passed;
  - a faster model wins a tie;
  - it runs once, then every call (including learning and complex tasks) uses the selected model;
  - if the selected model is proven down, the next qualified one takes over;
  - the live certification, the Control Center request, the CLI and the first-live-mission fallback are wired.
- **Replica fidelity:** the listing rows have no action button, only the expander.
- **Migrate:**
  - expand → DEV → Migrate → TEST2 → confirmation clicked once; one POST; the TEST2 badge appears;
  - the learned path is semantic;
  - EXISTING, "not offered" and an ambiguous name → no POST;
  - refused without the gate;
  - "deploy the document type … to TEST2" uses Migrate.
- **Edit:**
  - DEV / 1.0 → CURRENT / REQUESTED / CHANGE for exactly the two requested fields;
  - identifier and attributes kept;
  - the re-opened details show the new values;
  - an Edit where nothing differs → EXISTING;
  - a portal reaction that clears Description → not saved.
- **Clone:** keeps the source configuration; a clone without a new name → NEEDS_INPUT.
- **Promotion:** two verified migrates promote the learned path to DETERMINISTIC.
- **Request parsing:** environment tab, version and target.

The replica is `tests/doctypes_listing_support.py`: the live DDS markup of the Document Types listing, its expanded details, the Migrate menu with an optional confirmation, and the Edit / Clone drawer, with a server that keeps the records.

## Verification

(filled in below)

## Apply

```powershell
.\APPLY_V243R24_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R24_INSTALL.ps1
```

R24 includes R13–R23. If you keep your own config.yaml, the defaults apply without any change. To see or change them, copy the `qualification_*` keys under `model_portfolio` and `block_unrelated_changes` under `portal_operations` from the shipped config.yaml.

Then run **Certify Windows runtime** once (or `python -m hip_id_agent.cli certify-live-runtime`) to select the model.

## Validation boundary

- The Document Types flow is modelled on the screenshots of 2026-09-27: the row expander, the expanded details, Edit / Clone / Migrate, the Migrate menu and the Edit / Clone drawers. It was exercised in a real browser against that replica.
- Two details are not in the screenshots, and the agent handles both ways:
  - whether the live Migrate asks for a confirmation (with and without are tested);
  - the live toast wording.
- The model qualification was tested with a stand-in for Dell AIA. On the live machine it asks the real deployments once, during certification.
