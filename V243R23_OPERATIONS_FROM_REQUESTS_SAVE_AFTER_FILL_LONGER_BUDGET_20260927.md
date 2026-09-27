# V243R23: "deploy / migrate … the document type X" knows where to click; Save after a verified fill; a longer phase time budget (2026-09-27)

## What was asked

- Increase the timer.
- If I ask the agent to deploy or migrate a document type, or anything else, it should know where to click and perform that action.
- Once learning is complete after filling the form, the agent should be able to click the Save button; that option should be there.
- Learn everything.

## 1. Requests like "deploy the document type X to PROD"

**Before.** The Control Center task box planned *"deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD"* as:
1. open the Partner page;
2. look for a page-level "Deploy" button.

It did not know:
- that a document type lives on the Document Types listing;
- that its Deploy is an action on that object's row, directly or in the row's "More actions" menu;
- that "to PROD" is a value for the Deploy dialog.

**Now.** A request that names a HIP object and an object action runs as a **portal operation**, the same engine as input.json `operations` (R19).

- **HIP objects:** document type (source or target), data map, rule, transport profile (source or target), business flow.
- **Object actions:** deploy, migrate/promote, edit/update, clone/copy, merge, validate, delete, create/add, or "fill … and save/submit".

| Step | What the agent does |
|---|---|
| Where | Opens that object's listing, for example `…/securelink/doctypes` for a document type. |
| Which row | Searches for the name in the request (for example `XML_DellAutoASN_10_U-HAUL_ANS_IB`, or a quoted name). Without a name, it uses the name in input.json. A row whose own cell is exactly that name wins. |
| What to click | The row's action button, else the row's "More actions" menu, else the semantic resolver (icon-only buttons). **The label that worked is learned and tried first next time.** |
| The dialog | Fills the action's own dialog with the certified-skill engine: learned once, proved by a replay, then replayed (R19). "to PROD / UAT / DEV / QA" becomes the dialog's target environment. Other dialog values come from `objects.<phase>_<operation>` in input.json, for example `objects.source_document_type_deploy`. |
| Commit | Clicks Deploy / Migrate / Save / Submit … once, through the three-part gate: "Allow requested portal mutations", the phrase `ALLOW HIP MUTATION`, and `HIP_ALLOW_PORTAL_MUTATION=YES`. The outcome is reconciled from the write response and the page, and never retried blindly. |
| Check | Opens the listing again and confirms the row. |

Several actions can be chained: *"create the source transport profile from input.json, save it, then deploy it to UAT"* runs as create with Save, then deploy with UAT. **Plan** in the task box shows every step before anything runs.

A plain "fill …" request is not an operation and keeps its existing path. `universal_operator.route_object_operations: false` turns the routing off.

## 2. Save after the form is filled and verified

The full mission was deliberately no-save. It now has an **opt-in** Save step:
- Control Center, mission panel: **"Save each form after it is filled and verified"** plus the confirmation phrase;
- CLI: `--save-after-fill --allow-portal-mutation --confirmation "ALLOW HIP MUTATION"`, with `HIP_ALLOW_PORTAL_MUTATION=YES` set on the machine.

For each phase, the Save runs only when learning for that phase is complete:
- every input.json value has been read back from the live form;
- the exact-completion checkpoint and the judges have passed;
- the form is still open, before the agent moves to the next phase.

It then:
1. clicks the form's Submit / Save / Create once (the label that worked last time is tried first);
2. reconciles the outcome;
3. checks that the listing shows the object.

The result is written to `phase_save.json`. A save the portal rejects (for example "Name already exists") or cannot confirm **blocks the phase with `HIP_PHASE_SAVE_NOT_CONFIRMED`**; it is never retried and never reported as saved. With the gate closed, the forms are filled and left unsaved, and the run says so. Witness mode refuses the option.

## 3. Longer phase time budget

| Setting | Before | Now |
|---|---|---|
| `runtime_self_heal.max_phase_wall_seconds` | 1200 (20 min) | **3600 (60 min)** |
| `progress_extension_seconds` (R22: granted only while new fields keep being verified) | 600 | **900** |
| `max_progress_extensions` | 6 | **8** |

A phase that keeps making progress can therefore run up to 3 hours. The no-progress watchdog is unchanged: a phase that stops making progress is still recovered, and then handed to you.

## 4. What the agent learns

| Learned | Where |
|---|---|
| Where each action lives on each object's listing: the opener label and path, for example `more actions > deploy` | `data\hip_memory\portal_skills\<phase>.json` → `openers` (shown by `portal-skills` as `learned_actions`) |
| Each action's dialog (Deploy, Migrate, Merge …) and each value-dependent branch of it | `portal_skills\universal_<phase>_<operation>.json` (certified by replay) |
| The Save / Submit label a verified phase form was saved with | `portal_skills\<phase>.json` → `commit_labels` |
| Forms, row structures and branches | as before (R17–R19) |

## Tests

`tests/test_v243r23_task_operations_and_save.py` (18):
- request parsing, including "merge X into Y", chained create → save → deploy, and plain fill requests left alone;
- the task-box plan for a document type deploy;
- a real-browser free-text deploy that finds Deploy in the row's "More actions" menu, deploys, learns the label, and uses it first on the next deploy;
- the deploy refused without the gate;
- a verified form saved once (one POST) and confirmed in the listing, with the label learned;
- nothing posted without the gate;
- the mission saves only after verification and judges and before the handoff;
- the new time budget.

## Also fixed: settle after a commit

After a Save, a portal can redirect a moment later: the replica goes to the listing with a success message, and the live portal changes route and shows a toast. Under load, that late redirect interrupted the runner's next navigation (`Page.goto … interrupted by another navigation`). The runner now waits, after a successful commit, until the page's address and load state have been stable for two samples, up to 6 s, before it navigates again.

The replica harness also retries once on an interrupted navigation, as the live navigation code does.

## Verification

| Check | Result |
|---|---|
| Full suite (198 files) | 1,446 passed, 1 skipped. The one R19 real-browser failure under load was traced to the post-commit redirect race above. After the fix, the R19 operation tests and the R23 tests (22) passed three times in a row, each with three other browser tests running in parallel. The checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| R23 tests | 18 passed |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R23_INSTALL.ps1` R23 smoke checks | `R23_REQUEST_TO_OPERATION_OK`, `R23_SAVE_AFTER_FILL_AND_LEARNED_OPENERS_OK`, `R23_LONGER_PHASE_BUDGET_OK` (and the R22 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R23_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R23_INSTALL.ps1
```

R23 includes R13–R22. config.yaml needs no change. The shipped config.yaml sets the 60-minute budget; if you keep your own config.yaml, set `runtime_self_heal.max_phase_wall_seconds: 3600` there.

## Validation boundary

The deploy, row-action and save paths were exercised in a real browser against the replica operations portal, which has a listing, a "More actions" menu, a Deploy dialog and a server that keeps records. The live Document Types listing's exact Deploy/Migrate markup is not in the knowledge base. The agent finds the action by its label (Deploy, Migrate or Promote, directly or in the row's menu), falls back to the semantic resolver for icon-only buttons, and learns what worked.
