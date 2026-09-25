# V243R19: learning is saved only once a replay proves it; edit / clone / merge / deploy come from input.json; certified forms replay fast (2026-09-25)

## What was asked

- Learn to fill correctly from input.json, and **save the learning only when learning is complete and a deterministic replay works**.
- Handle operations such as **deploy, edit, merge and clone**, and fill their values from input.json.
- Fill everything, deterministically.
- **Re-learn when something is new**, for example a section whose fields depend on the value selected.
- Run faster.

## How it works now

Everything the agent knows about a form is a **skill**. A skill covers one form (a phase, or an operation's dialog), one **operation** and one **branch**. A branch is the set of values of the choice fields that change the form's shape. Examples:
- Interface Type AS2 shows an "AS2 Settings" section.
- Target Environment PROD shows Change Ticket and Approver.
- Data Format EDI shows the separator fields.

| Step | What happens |
|---|---|
| **learn** | The adaptive engine (live discovery, models, self-repair) fills every input.json value and proves each by exact read-back. The result is only a **candidate**: nothing is saved as knowledge yet. |
| **prove** | The candidate is **replayed deterministically on a fresh form**, using only the learned bindings. There are no model calls and no exploration. Every value must read back exactly, and every field must bind to the same control as during learning. The operation runner reopens the form and proves the skill in the same run. A mission phase (which cannot reopen its form) proves it on the next run. |
| **certify / save** | Only a passing replay saves the skill (`data\hip_memory\portal_skills\<form>.json`) and merges its form structure into long-term memory (`form_structure_memory`). A replay that does not reproduce the learning is discarded, and nothing is saved. |
| **fast replay** | Later runs with a certified skill take the fast path. These layers are skipped: website understanding, the AutoWebGLM observation, the LLM form planner, the golden-screenshot vision checks, the per-field AutoWebGLM model decision and the per-field MCP/vision target re-proof. One pass plus one read-back of the whole form replaces the second verification pass. |
| **re-learn** | Anything new sends the run back to learning, and the new skill must be certified again: a new input field, an unseen branch, a field revealed by a selected value, a required control not covered, a missing option, or a field that binds to a different control than when learned. |

**Which choice fields are branch fields is learned too.** Suppose two skills of the same operation have forms of different shapes. The choice values that differ between them become branch fields. If two skills have the same shape despite a different value, that field is dropped as a branch field. A dropdown such as Deployment Group, whose value does not change the form, therefore does not force re-learning.

## Operations from input.json

```json
"operations": [
  {"phase": "source_transport_profile", "operation": "edit", "target": "SFTP_U-HAUL_ASN_PC_SRC_IB",
   "values": { "...": "..." }, "commit": true},
  {"phase": "source_transport_profile", "operation": "deploy", "target": "SFTP_U-HAUL_ASN_PC_SRC_IB",
   "values": {"target_environment": "PROD", "change_ticket": "CHG0012345", "approver": "release-manager"},
   "commit": true, "expect": ["Deployed PROD"]}
]
```

```powershell
python -m hip_id_agent.cli run-operations input.json                      # learn + fill, no Save
$env:HIP_ALLOW_PORTAL_MUTATION = "YES"
python -m hip_id_agent.cli run-operations input.json --allow-portal-mutation --confirmation "ALLOW HIP MUTATION"
python -m hip_id_agent.cli portal-skills                                  # certified / candidate / stale skills
```

The backend offers the same: `POST /api/operations/run` and `GET /api/portal-skills`.

For each operation the runner does the following.

**1. Opens the surface**
- It opens the listing, searches for the target, and clicks the row's action (Edit, Clone, Merge, Deploy, Migrate…).
- If the action is not on the row, it uses the row's "More actions" menu.
- If neither is found, it falls back to the live semantic affordance resolver, which handles icon-only buttons.
- `create` opens + Add.
- Create, edit and clone fill the phase form. Merge, deploy and other actions fill their own dialog, learned as `universal_<phase>_<operation>`.

**2. Fills** it with the skill engine above.

**3. Commits** only when all of these hold:
- `commit: true`;
- the fill was exact;
- the skill is certified;
- the three-part mutation gate is open (flag, `HIP_ALLOW_PORTAL_MUTATION=YES`, phrase).

The commit button is clicked once and never retried blindly. The outcome is reconciled with the existing mutation-outcome classifier: write responses, UI signals and the listing. The commit label that worked is remembered with the skill.

**4. Verifies** the effect:
- the surface closes or reports success;
- the listing shows the object, and any `expect` text such as the new status;
- for a merge, the object merged into.

To **create and save** a new object in the portal, use `{"operation": "create", "commit": true}`. The Create/Save button is pressed only once the form's skill is certified, that is, once the fill has been proven deterministic. The first run of a new form therefore learns it, proves it on a fresh form in the same run, and then saves.

`values` defaults to `objects.<phase>`. See `examples\operations_example_input.json`. Opening Deploy (or Delete) is itself a portal mutation, so it also needs the gate. Without the gate such an operation is not even opened. Edit, clone and merge forms are learned and filled, then left unsaved.

## Live-portal bugs found while building this

| # | Problem | Fix |
|---|---|---|
| 1 | **A dropdown option whose text contains a mutation word was blocked** by both safety guards. Affected values include "Delete" as a Post Transfer Action, and any option reading Update, Remove, Enable, Disable, Confirm… The guards read the option as a Delete button. A combobox whose current value was "Delete" could not even be opened. | An option of a combobox-owned listbox, and a text/combobox field's own value, are form values, not actions. Row menus (`role=menuitem`) and buttons stay guarded. |
| 2 | **"None" could never be chosen** as a real option (Post Transfer Action = None): it was treated as an empty placeholder. | "None" is a placeholder only when it is not the value asked for. |
| 3 | **An Edit / Clone Transport Profile form failed the surface check.** The check required the title "Create Transport Profile". | Create / Edit / Clone / Update titles are accepted. |
| 4 | **A radio or checkbox clicked through its label had no broker provenance.** The executor's first pass then ended with `HIP_PHASE_AUTHORITATIVE_INTERACTION_NOT_VERIFIED` and all its work was redone by the second pass (Data Map variant: about 23 s wasted per run). | A label click is credited to its input. |
| 5 | **A form opened on its own route (Edit → `/…/edit`) blocked the Save** with `HIP_MUTATION_PREDISPATCH_ROUTE_DRIFT`. | Once the form is proven open, its route becomes the expected route. Drift after that is still refused. |
| 6 | **After one verified commit, every later mutation in the session was refused** (`HIP_MUTATION_QUARANTINE_ACTIVE`). The commit was never reconciled. | Each commit is reconciled from write responses, UI signals or the listing. A guarded row action that only opened its dialog is reconciled as `opened_surface_no_write`. An unclear outcome keeps the quarantine. |
| 7 | **A button-style radio group (Rule "Priority") had no provenance** either: the option clicked is not the control the executor bound. The Rule's first pass was always redone. | A click on one option is credited to its whole group. The error now names the fields concerned. |
| 8 | **A row action could open the wrong row** ("TP_BETA" also matches "TP_BETA_COPY"). | Rows with an exact name cell come first. The same applies to listing verification. |
| 9 | The replay settle wait: 0.25 s after every click. | 0.1 s during a certified replay. Each field still waits for its own exact committed state. |

The replica kit also gained two fixes. Its dropdowns now update `aria-selected` on commit, and its popup sits above the sticky footer, as DDS does.

## Speed

Without models (the replica), the time per field is spent on real portal settling: scroll, animation, Angular commit. Replay is about 1.2× faster there:
- Transport Profile edit: learn 17.6 s, certified replay 12.4–14.9 s;
- Deploy PROD: learn 9.3 s, replay 8.4 s.

With a model, learning pays for a model round trip on every field, and a certified replay pays none. In the test, a stand-in model with 0.4 s per decision was used:
- the learning run made 21 model calls;
- the replay run made 6: search, row action, Save and listing, and none for a form field;
- the replay's fill was faster than the learning fill.

With gpt-oss-120b on the live portal, each skipped call is a full model round trip. So are the per-cycle understanding, planner and vision checks that replay skips.

The one-time cost is the proof: a new skill is filled twice (learn, then replay) in the run that learns it.

## Tests

- `tests/test_v243r19_portal_skills.py` (16):
  - skill lifecycle;
  - candidate discarded;
  - stale skill and re-learning;
  - a new field teaches a branch field;
  - branch fields learned and dropped from form shapes;
  - binding identity ignores value-based row fingerprints;
  - value-free storage;
  - commit-label memory;
  - operation parsing;
  - the three-part gate;
  - metadata keys;
  - "None";
  - replay skips the model call and the per-field re-proof, but not for a commit;
  - Edit/Clone surface gate.
- `tests/test_v243r19_operations_real_browser.py` (4, real Chromium, `tests/operations_portal_support.py`):
  - edit learned, proved, committed, then replayed with no per-field model call;
  - AS2 re-learned as a new branch;
  - deploy UAT, then deploy PROD with its revealed fields learned;
  - clone and merge;
  - nothing saved without the gate.
- `tests/test_v243r19_replay_all_phases.py` (2):
  - a button-style radio group's choice has provenance, so the first pass holds;
  - Document Type is learned, then replayed in one certified pass plus one read-back.
- `tests/test_v243r17_structure_learning.py`: the first run now leaves only a candidate. The second run replays it, certifies it, and only then saves the form structure.

## Verification

| Check | Result |
|---|---|
| Full suite (193 files) | 1,399 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| R19 tests | 22 passed: 16 unit tests, 4 real-browser operation tests, and 2 phase-replica replay tests |
| Operation sequence on the replica (one browser) | Edit (learn, prove, save) → edit (replay, save) → edit to AS2 (new fields, re-learn, save) → clone → deploy UAT → deploy PROD (revealed fields learned) → merge. All saved exactly and verified in the listing. A second run replayed edit and deploy PROD from certified skills. |
| Every phase replica (variant forms) | Data Map, Document Type, Rule, Transport Profile, BizFlow Flow Details: learn → candidate → the next run replays and certifies → certified replay, all exact |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R19_INSTALL.ps1` R19 smoke checks | All pass (and the R18 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R19_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R19_INSTALL.ps1
```

R19 includes R13–R18. config.yaml needs no change. The new `portal_skills` and `portal_operations` settings have safe defaults (see `config.example.yaml`). `data\hip_memory` is preserved.

## Validation boundary

- The operations were exercised on a replica portal (listing, row actions, More actions menu, dialogs, server-side records). The live portal's Merge action is not in the knowledge base. It is handled as a generic row action by its label, and its dialog is learned like any other form.
- Mission phases prove a new skill on their next run, because a mission cannot reopen a half-filled phase form just to prove it.
- The section judges still run once per mission phase.
