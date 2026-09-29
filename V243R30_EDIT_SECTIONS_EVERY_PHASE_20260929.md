# V243R30: every phase's Edit section learned, read and edited through (2026-09-29)

## What was asked

- "Open the Transport Profile link, click the expand button, click the Edit button and capture all the values."
- "It can edit."
- "The same needs to be done for the BizFlow and all the phases."
- "Save it, so that it already has knowledge of the Edit section too."

## What the agent now does

### 1. Learn an object's Edit section (read-only)

For each phase (Transport Profile source and target, Business Flow, Data Map, Rule, Document Type source and target), `learn_edit` works like this:

1. **Open the listing link** of the phase (`PHASE_URLS`).
2. **Search the object**:
   - the name comes from the request or input.json (`objects.<phase>`);
   - without a name, the listing's first row is used.
   The row whose name cell is exactly the object is chosen, so `SFTP_…_SRC_IB` never opens `SFTP_…_SRC_IB_OLD`. Several candidates give NEEDS_INPUT.
3. **Click the row's expander** ("Expand the row"). The expanded details are proven to belong to that row, then **Edit** is clicked. This is the same semantic path Edit operations use (R24).
4. **Wait for the Edit form itself**, not the listing behind it. The portal opens the drawer or page first and fills it from the record a moment later, and a form read before that would be empty.
5. **Read every value**:
   - every **tab** of a wizard (the BizFlow: Flow Details, Configure Source, Configure Target(s), Configure Routing; the first tab is selected again at the end);
   - every **collapsed section** (e.g. the BizFlow's collapsed "2 ::: Step");
   - every **repeatable row**, also the unlabelled ones DDS shows after the first.

   Each field records:
   - label, section, tab, row, and kind (text, dropdown, multi-select, radio group, switch, checkbox, file);
   - whether it is **required** or **read-only in Edit** (e.g. Profile Name, Business Flow Name, Map Identifier, the Rule's Name);
   - the options a dropdown offers, read from its own list without opening it;
   - the **input.json key** it maps to. The mapping uses the fill engine's own binder, so a captured field and a filled field are the same field; a row beyond input.json's rows takes the key of the same field in another row.

   Two kinds of value are handled specially:
   - a password or secret field is never read out (`********`);
   - an uploaded file is read by the name the form shows next to "Browse Files".
6. **Close with Cancel / Close / Back**:
   - nothing is typed, chosen or saved;
   - a write request seen during the capture fails the step and nothing is learned from it;
   - an Edit page returns to its listing.
7. **Remember it:**
   - **`<memory_dir>/edit_sections/<phase>.json` (the knowledge, value-free):** how to open the Edit section (listing → search → expand → Edit), the surface (drawer or page, title, tabs, sections, the Save and Cancel labels), every field's structure, the read-only fields, the input.json keys it covers and the fields input.json does not have. No object's values are stored there.
   - **The run folder (the values):** `edit_values.json` (every field with its current value) and `edit_input.json`. The second is the object's current values shaped as input.json `objects.<phase>`: change it and run it as an Edit operation.
   - **The learned path:** the Edit path counts as a verified outcome when the opened form was that object's (its name field). Twice verified, it becomes DETERMINISTIC (R24).

**How to run it:**
- **Task box:** "capture the edit values of transport profile SFTP_U-HAUL_ASN_PC_SRC_IB". Your own sentence works too ("open Transport proflie … click the expand button … Edit … capture all the values … same for the Bizflow and all the pashe …"). It plans every phase, the Transport Profiles and the BizFlow first, and never saves.
- **input.json:** `"operations": [{"phase": "biz_flow", "operation": "learn_edit"}]`.
- **CLI:**
  ```powershell
  python -m hip_id_agent.cli learn-edit-sections .\input.json                  # all phases
  python -m hip_id_agent.cli learn-edit-sections --phases transport_profile,biz_flow
  python -m hip_id_agent.cli learn-edit-sections --show                        # what is known
  ```
- **Control Center:** the "Edit sections known" tile shows n/7 phases, each Edit form's title, field count and tabs. `GET /api/learning/edit-sections` returns the knowledge.

### 2. Edit through it

Every Edit operation now uses the Edit section:

1. **It reads the whole Edit form first** (every tab, read-only) and refreshes the knowledge with it.
2. **A read-only field stops the edit.** If a requested change is to a field the form keeps read-only (renaming a Transport Profile or a Rule), the result is **NEEDS_INPUT**: *"read-only in Edit Transport Profile: the portal does not let Edit change 'Profile Name'; nothing was changed"*, with Clone suggested for a new name. Before, the fill tried the disabled field and failed.
3. **Only the requested values change** (R24 EDIT SAFETY: current / requested / change, and nothing is saved when another field changed).
4. **The learned Save is clicked first** ("Update" on the Transport Profile Edit form, "Submit" on the Data Map). The three-part mutation gate is unchanged, and the click happens once.
5. **The save is read back.** After the listing shows the object, the requested values are read from the expanded details. The Transport Profile, BizFlow and Data Map details show only a few fields, so where they do not show every edited field, **the Edit form is opened again (read-only)** and the values are read there. A save the portal did not keep ends as `committed_values_not_seen` (FAILED), not SUCCESS.
6. **A BizFlow edit is proved in one operation.** Its tab skills used to be proved only "on the next run", so the first BizFlow edit stopped at `commit_waiting_for_certified_skill`. An Edit page can be opened again as a whole, so the operation now does this in one go:
   1. fill the tabs;
   2. reopen the Edit page;
   3. replay the fill deterministically, which certifies the skills;
   4. save once.

Settings (`edit_sections` in config.yaml; every one has a default):

| Setting | Default |
|---|---|
| `form_wait_seconds` | 30 |
| `capture_on_edit` | true |
| `block_read_only_changes` | true |
| `verify_by_reopening_edit` | true |

## Found and fixed on the way

- **A signed-in HIP page was taken for Dell SSO.** The SSO keywords were matched as substrings of the URL, and `ping` is one of them. Any HIP URL with an object name containing "MAPPING" (the BizFlow's Edit page `…/bizflows/edit/U-HAUL_PC_856_ANS_MAPPING_OB`), "shipping", "author" and so on stopped with `HIP_AUTH_SESSION_EXPIRED`. The keywords are now matched as URL words, with forms such as `saml2`, `oauth2`, `authorize` and `authentication` still recognised. A `/hybrid-integrations/` or `/securelink/` route is never treated as a sign-in page. Real sign-in URLs are still recognised (myaccess.dell.com, login.microsoftonline.com, Okta, `/idp/SSO.saml2`, `/as/authorization.oauth2`, `/signin`, `/auth/realms`).
- **The Edit form was read too early.** After Edit on a drawer, the listing's own panel fields counted as "the form is visible" while the drawer still showed "Loading…". Edit / Clone operations and the learning now wait for the Edit surface itself: a dialog or page with its own controls, not loading, with a control count stable over two reads.
- **The listing behind a drawer leaked into the capture** (its search box and the expanded row's read-only details). Only controls inside the Edit surface count now, and a table's own search box is never a field.
- **A field read-only in Edit lost its key to a neighbour.** In Edit the Rule's Name is disabled, and the fill binder penalises disabled controls, so "Document Type Name (Version)" took the `name` key. The capture's mapping binds structurally.
- **The task box took a value for an operation.** In "set Post Transfer Action to Delete", "Delete" was read as a Delete operation. A verb right after "to", "as", "=" or ":" is now a value.
- The task box recognises "transport proflie"-style spellings.

## Tests

`tests/test_v243r30_edit_sections_every_phase.py` (13). The new replica `tests/phase_listing_support.py` provides the Transport Profile, Business Flow, Data Map and Rule listings, with the DDS table, "Table search", the row expander, and the expanded details (environment tabs, a few read-only details, Edit / Clone / Deploy).

Edit opens the phase's full create-form replica, filled by the "portal" from the stored record, with the Edit-only read-only fields disabled:
- **as a drawer** over the listing: Transport Profile (also shows the audit fields and an account password in Edit) and Data Map;
- **as its own page:** Business Flow (4 tabs, a collapsed step, unlabelled rows, the routing table's search box) and Rule.

Save posts the form; the server keeps it.

**Real browser: learn**
- **Transport Profile:**
  - the exact row is found among `_OLD` look-alikes;
  - all 15 input.json values are read exactly, plus tags, checkboxes and the audit fields;
  - Profile Name is read-only;
  - the password is masked everywhere;
  - the form closes with Cancel, with no write request;
  - `edit_input.json` holds the values.
- **Knowledge:**
  - title, Update / Cancel labels, path, read-only fields, keys and portal-only fields;
  - no object value is in the file;
  - the Edit path is recorded and verified.
- **Business Flow:**
  - the 4 tabs are read in order, and the collapsed step is opened;
  - both unlabelled identifier rows and both process steps are read;
  - the routing search box is not a field;
  - it returns to the listing, and nothing is saved.
- **Data Map, Rule, Document Type:**
  - the uploaded file name;
  - the placeholder Version;
  - the Rule's read-only Name keeps its key;
  - the Document Type without a name is taken from the first listing row.
- **Your request:** learn_edit for all 7 phases, the Transport Profiles and the BizFlow first. Run in one browser, it gives SUCCESS for the three available listings, with nothing saved.

**Real browser: edit**
- **Transport Profile edit:**
  - only the two requested values change;
  - the learned "Update" is clicked;
  - every other value is kept;
  - the values are read back from the reopened Edit form.
- **Rename of Profile Name:** NEEDS_INPUT, with no POST.
- **BizFlow edit:** learned, reopened, replayed (certified), saved once, read back. Before R30 this was `commit_waiting_for_certified_skill`.
- **A save the portal did not keep:** caught by the read-back (`committed_values_not_seen`).
- **Without the mutation gate:** the form is read, never saved.

**Units:**
- the task-box parsing (learn vs edit, the value-not-operation fix);
- radio groups, secrets, placeholder values and input.json row shapes;
- the read-back comparison;
- the SSO URL words;
- CLI / backend / Control Center wiring.

## Verification

| Check | Result |
|---|---|
| R30 tests | 13 passed (real browser: every phase learned; Transport Profile, BizFlow, Data Map and Rule edits; read-only guard; read-back; gate) |
| Closest suites (R19 operations and skills, R23 task operations and save, R24 row-panel operations) | 58 passed. One R19 assertion was updated: a certified Edit replay consulted the per-action model at most 6 times; with R30 it also reads the saved values back from the reopened Edit form (search, open, Cancel), 10 in all. With the read-back switched off the original ≤ 6 holds, and still no call is made for a form field. |
| Full suite (205 files) | 1,537 passed, 1 skipped. Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| Package `HIP_PORTAL_V243R30_FINAL_FULL_E2E_20260929.zip` | Every tracked file identical to the branch; the three wheels match the source; the R13–R30 install smoke checks (55) pass from the extracted package; 67 tests pass from it (R30, R29, R23 task operations and save, R24 row-panel operations, input contract incl. the upload assets) |
| `VERIFY_V243R30_INSTALL.ps1` R30 smoke checks | `R30_EDIT_SECTION_CAPTURE_OK`, `R30_EDIT_OPERATIONS_USE_EDIT_SECTION_OK` (and the R29 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R30_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R30_INSTALL.ps1
python -m hip_id_agent.cli learn-edit-sections .\input.json
```

R30 includes R13–R29. No config.yaml change is needed: the `edit_sections` section has defaults.

## Validation boundary

- **The live listings.** The Transport Profile, BizFlow, Data Map and Rule listings are modelled on the recorded Document Types listing (R24) and on your description (expand button, then Edit). Whether each live Edit opens as a drawer or as its own page is not recorded; both are handled and tested.
- **When the live portal differs:**
  - if a live Edit shows actions on the row itself, or in a "More actions" menu, the existing R23 path handles it;
  - if a live Edit form has a different Save label, it is learned on the first capture.
- **Credentials:** a password or secret field is never read or stored. A certificate or key upload shows only its file name.
