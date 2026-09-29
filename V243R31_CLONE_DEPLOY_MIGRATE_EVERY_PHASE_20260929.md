# V243R31: Clone, Deploy and Migrate on every phase: performed, learned, remembered (2026-09-29)

## What was asked

- "Deploy, Migrate and Clone too, for all the phases, and it can perform them."

R30 did this for Edit. R31 does it for the other row actions of every phase: Transport Profile, Business Flow, Data Map, Rule and Document Type.

## What was wrong before R31

R24 built Clone and Migrate for the Document Types listing. R19 built Deploy for a Transport Profile page without an expander. On the other listings (the expanded row's Edit / Clone / Deploy / Migrate), the replicas showed:

| Action | Before R31 |
|---|---|
| Deploy that opens a **dialog** (Transport Profile, Target Environment field) | Its button declares a popup, so the dialog was taken for a menu. The menu was empty, and the agent answered *"TEST1 is not offered by Deploy"*. |
| Deploy that opens a **menu** (Business Flow) | The Deploy click is a guarded mutation and was never reconciled when it only opened a menu. The real choice (TEST2) was then refused: `HIP_MUTATION_QUARANTINE_ACTIVE`. The next operations in the same browser failed too. |
| Deploy with a **confirmation** only (Data Map: "Deploy X from DEV to TEST1?") | No path for it. |
| A target already there, when the Version is shown as text | Not recognised; the portal rejected the deploy (409). |
| A target the dialog does not offer (PROD from DEV) | The agent tried to fill it for 136 s, then gave up. |
| Clone | Checked only against the listing's details (the name). |
| Learning Clone / Deploy / Migrate | Nothing was learned or remembered. |

## What the agent now does

### 1. Perform: every phase, every Deploy shape

After clicking a row action, the agent looks at **what actually opened** (`_OPENED_JS`):

**A menu** (Migrate > TEST1 / TEST2; Business Flow's Deploy):
- the requested environment must be offered, otherwise NEEDS_INPUT with the offered ones;
- the choice is clicked once;
- the portal's confirmation is answered once;
- the listing must then show the environment.

**A dialog with fields** (Transport Profile's Deploy):
- the dialog is read first;
- a requested value it does not offer (PROD when it offers TEST1 and TEST2) is NEEDS_INPUT within seconds, and the dialog is cancelled;
- otherwise the dialog is filled (certified skill), Deploy is clicked once, and the listing is verified.

**A confirmation** (Data Map's Deploy: "Deploy DELLCoX… version 1.0 from DEV to TEST1?"):
- the portal names the target, and it must be the requested one;
- a different target is cancelled with NEEDS_INPUT ("Deploy from DEV goes to TEST1");
- the right one is confirmed once and verified.

**No Deploy on the listing** (Rule, Document Type): the environment is reached with **Migrate**, as in R24.

**Safety:**
- A guarded Deploy that only opened a menu or confirmation, with no write request, is reconciled as "opened a surface, no write". The one real mutation (the choice or the confirm) can then be dispatched, once.
- A target that already holds the version is **EXISTING**, and nothing is clicked. The Version is now also read when it is shown as text ("Version : 1.0").
- The three-part mutation gate is unchanged.

**Clone** (every phase: drawer or page):
- the Clone form is read first and remembered;
- a clone needs a new name, otherwise NEEDS_INPUT;
- a value read-only in the Clone form cannot be changed;
- the form's own Save ("Create", "Submit", "Save") is clicked once;
- the listing must show the new object;
- then **the new object's Edit form is opened**:
  - every requested value must be there (else `committed_values_not_seen`);
  - every other value is compared with the source (`kept_from_source`, `differs_from_source`).
- A Business Flow clone is proved by a replay on the reopened Clone page in the same operation (as R30's BizFlow edit).

### 2. Learn: read-only, remembered

`learn_clone`, `learn_deploy` and `learn_migrate` join R30's `learn_edit`:

- **Clone:**
  - the Clone form is opened, read completely (every tab, section and row), and closed with Cancel;
  - the knowledge records which fields a clone may change.
- **Migrate / Deploy:**
  - the object's row is expanded;
  - for **every environment tab** it has, the agent reads where the action goes:
    - **a menu the page already holds is read without any click**;
    - **Migrate** is opened, read and closed;
    - **a guarded Deploy button may act at once, so it is opened only with the three-part mutation gate**. Even then only its menu, dialog or confirmation is read, and cancelled; nothing is confirmed.
  - Without the gate, the result is BLOCKED with the reason, and the first authorized deploy learns it: every Deploy / Migrate operation records what it saw.
- **The knowledge:** `<memory_dir>/action_sections/<action>/<phase>.json`, value-free:
  - section kind (menu, dialog or confirmation);
  - the targets per source environment (`DEV > TEST1, TEST2`, `TEST1 > TEST2`, `TEST2 > PROD`);
  - the confirmation's wording without the object's name (`Deploy <object> version <version> from DEV to TEST1?`);
  - the dialog's fields and the Save / Cancel labels.

**Operations use it:**
- **A guarded Deploy is not even opened** for a target it was learned not to offer from that environment. The answer names the route, learned from the Migrate / Deploy menus: *"PROD is not offered by Deploy from DEV (learned: TEST1, TEST2); PROD is reached through DEV > TEST2 > PROD (migrate to TEST2 first); nothing was clicked"*.
- **Refusals name the same route**, when it is known, for a live menu, dialog or confirmation.

**How to run it:**
- **Task box:**
  - "clone transport profile X as Y and save";
  - "deploy the bizflow X to TEST2";
  - "migrate data map X from DEV to TEST1";
  - "learn the deploy and migrate sections of the bizflow";
  - "show me every action of the transport profile X";
  - "capture the clone, deploy and migrate options for all the phases".
- **CLI:**
  ```powershell
  python -m hip_id_agent.cli learn-action-sections .\input.json                         # edit, clone, deploy, migrate; all phases
  python -m hip_id_agent.cli learn-action-sections --actions deploy,migrate --phases biz_flow
  python -m hip_id_agent.cli learn-action-sections --actions deploy --allow-portal-mutation --confirmation "ALLOW HIP MUTATION"   # opens Deploy, never confirms
  python -m hip_id_agent.cli learn-action-sections --show
  ```
- **input.json:** `"operations": [{"phase": "rule", "operation": "learn_migrate"}]`.
- **Control Center:** the tile is now "Edit / action sections known" and adds `clone n/7 • deploy n/7 • migrate n/7`. `GET /api/learning/edit-sections` also returns `action_knowledge`.

## Found and fixed during validation

**The read-back compared with the raw request.** The new Clone read-back (and R30's Edit read-back) compared the saved values with the raw request. In the R19 clone test, the request asked for the legacy SFTP-HAFT Deployment Group `dce-default-sender`. The phase's own policy (`deployment_group_policy`) fills the portal's current group, `da-sender-sftphaft-dce-shared`, instead, and that is what was saved. The read-back therefore reported a value the portal did not keep.

The read-back now compares with the values the phase compiler resolved for the fill (`_effective_requested`). A value that really was not kept is still caught.

## Tests

`tests/test_v243r31_clone_deploy_migrate_every_phase.py` (9). The listing replica (`tests/phase_listing_support.py`) gains:
- **Clone:** the phase's form, titled "Clone …", filled from the record; the name is editable, and an existing name is rejected.
- **Migrate:** a menu (from DEV: TEST1, TEST2; from TEST1: TEST2; from TEST2: PROD), then a confirmation.
- **Deploy**, in three shapes:
  - a dialog with Target Environment (Transport Profile);
  - a menu plus a confirmation (Business Flow);
  - a plain button whose confirmation names the next environment (Data Map).
  A Rule has no Deploy.
- **A server** that keeps the environments and the clones.

**Real browser:**
- **Transport Profile:**
  - already in TEST1: EXISTING, nothing clicked;
  - PROD: NEEDS_INPUT offering TEST1, TEST2, in seconds;
  - TEST2 through the dialog: deployed once, verified;
  - Migrate to TEST1 through the menu: migrated once, verified;
  - the dialog is remembered.
- **Business Flow:**
  - Deploy menu to TEST2 (the opener reconciled as `opened_surface_no_write`);
  - Migrate to TEST1;
  - Clone on its own page: the new object's Edit form holds the new name and every other value of the source.
- **Data Map:**
  - the confirmation goes to TEST1, so TEST2 is cancelled with NEEDS_INPUT and TEST1 is confirmed once;
  - Clone with a changed Map Class;
  - a clone with the source's name: NEEDS_INPUT;
  - the confirmation's wording is remembered without the object's name.
- **Rule:**
  - "deploy to TEST1" goes through Migrate;
  - migrating again: EXISTING;
  - Clone keeps every source value.
- **Learning, read-only:**
  - the Clone form;
  - Migrate menus for DEV and TEST1, read without a click;
  - Deploy BLOCKED without the gate;
  - with the gate, the dialog is read and cancelled;
  - no write request.
- **The learned route:**
  - a deploy to TEST2 and the Migrate menus of an object in DEV / TEST1 / TEST2 teach DEV > TEST2 > PROD;
  - "deploy … to PROD" from DEV is then NEEDS_INPUT naming that route, without clicking Deploy.

**Units:**
- the request parsing (clone new names incl. the Business Flow's nested name, learn requests for chosen or all actions, real Deploy / Migrate requests unchanged);
- routes;
- the offered-value check;
- the confirmation wording;
- kept-from-source;
- CLI / backend / Control Center wiring.

## Verification

| Check | Result |
|---|---|
| R31 tests | 9 passed (real browser: every Deploy shape, Migrate, Clone on every listing; read-only learning with and without the gate; the learned route) |
| Closest suites (R19 operations real browser, R24 row-panel operations, R30 Edit sections) | 35 passed on the final code (R19 + R24: 22, R30: 13) |
| Full suite (206 files) | 1,546 passed, 1 skipped. The first run found the R19 clone read-back case below (fixed, then R19 / R24 / R30 / R31 rerun). Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| Package `HIP_PORTAL_V243R31_FINAL_FULL_E2E_20260929.zip` | see the package verification commit |
| `VERIFY_V243R31_INSTALL.ps1` R31 smoke checks | `R31_ACTION_SECTIONS_OK`, `R31_CLONE_DEPLOY_MIGRATE_EVERY_PHASE_OK` (and the R30 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R31_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R31_INSTALL.ps1
python -m hip_id_agent.cli learn-action-sections .\input.json
```

R31 includes R13–R30. No config.yaml change is needed.

## Validation boundary

- **Recorded vs modelled.** The Document Types listing (Migrate menu, Clone drawer) was recorded from the live portal in R24. How Deploy looks on the live Transport Profile, Business Flow and Data Map listings is not recorded, so the three common shapes (dialog, menu, confirmation) are all handled and tested. A Deploy that navigates to its own page with a form is handled as a dialog with fields (R19).
- **Deploy learning without the gate.** A guarded Deploy is never clicked without the gate. On a portal that does not render its menu before the click, `learn-action-sections` without the gate learns Deploy only from the first authorized deploy. Migrate, Clone and Edit are learned without the gate.
