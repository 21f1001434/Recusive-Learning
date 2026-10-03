# V243R39 — BizFlow end to end, and every phase learns every operation (2026-10-03)

This round answers two requests:

* **BizFlow:** "first it clicks + Add, then a card is there, it clicks the link, the form opens, it fills each tab and clicks Next at the bottom; in the last tab there are options — it needs to learn everything."
* **Every phase should learn Create, Edit, Migrate, Clone and Deploy.**

Both were rebuilt against a local copy of the portal that behaves like the live one, and proven with the real mission code.

## 1. The portal copy now behaves like the live BizFlow

`tests/hip_portal_sim.py` (the R38 HTTPS copy of every module at its real `developer.dell.com` path):

| Live portal (golden BizFlow-FD / CS / CR / Deployed) | Copy |
|---|---|
| "Manage Biz Flow" listing with + Add, expandable rows | ✓ (the R30/R31 listing replica: rows, environment tabs, Edit / Clone / Deploy / Migrate, records kept by the server) |
| + Add → flow templates: "Search flow templates", the **B2B-Flow-PubSub-Template** card (Inbound / Outbound tags) | ✓ two cards; the template name is a link (`javascript:void(0)`), and its ⋮ menu also holds "Create Biz Flow" |
| The link opens **Create Biz Flow**: Flow Details, Configure Source, Configure Target(s), Configure Routing | ✓ |
| Tabs ahead are not reachable; **Next** at the bottom moves on | ✓ locked tab headers (`aria-disabled`); Next checks the tab's required fields and refuses with "Please fill the required fields: …" |
| Flow Details: Reset · Next; middle tabs: Previous · Reset · Next; Configure Routing: Previous · Submit | ✓ (Reset clears the tab, Submit is recorded — both are traps the agent must never press) |
| Configure Routing: Table search, + Add (Create Rule drawer), columns with ⋮ menus, Action ↑ ↓ ⋮ per rule, pager | ✓ |

Every other module's listing is the R30/R31 listing replica too, so a mission can learn Edit / Clone / Migrate / Deploy on the same portal it creates on.

## 2. Why BizFlow did not finish, and what changed

Running the real `FullDummyFillE2EFlow` on that copy reproduced each failure:

| What happened | Cause | Fix |
|---|---|---|
| The agent went through the card's ⋮ menu instead of the link | the card link's `href="javascript:void(0)"` was rejected as "another page" | an in-page link (`javascript:`, `#`, no href) or a link below the module is followed; Back / View Template are never taken |
| `HIP_BIZFLOW_TAB_NOT_OPENED: could not prove active BizFlow tab 'Source Details'` | a tab ahead is locked; the agent only clicked its header | `_ensure_bizflow_tab_open` walks the wizard with its own **Next** (one tab at a time, each move proven); a header that does not open its tab moves the wizard instead |
| Next pressed the wrong button | "Next" was the first `button:has-text('Next')` — the routing table's pager has one | `_WIZARD_BUTTON_JS` finds the wizard's own Next / Previous: inside the Create Biz Flow form, never in a pager, table, drawer, dropdown or tab list |
| The portal refused Next and the agent did not know why | no read-back after Next | the alert and the fields the portal marks invalid are read; the tab is filled again from input.json and Next tried once more; otherwise `HIP_BIZFLOW_NEXT_BLOCKED: … (missing: Business Flow Name *, …)` |
| Configure Source was filled into Flow Details | the row "+" finder took the **Flow Details tab header** for the Attributes "+" (a small button near "Attributes"), the wizard jumped back | tab lists, tabs, the wizard bar, pagers and Back / Previous / Next / Reset / Submit are never row adders |
| The routing tab took minutes and typed wrong values | the legacy dummy pass ran first: it typed guessed values (a rule name into a drawer with no rule field), opened every dropdown — even "Items per page" | with the goal engine on (default) each tab is filled by it alone, rows through the live "+"; repairs go to the goal engine |
| "Live check: 0/54" after every Next | the live map reads the tab on screen only | values verified exact **on their own tab** stay counted once that tab is no longer shown (same open form only; a reload or a reopened form starts empty). A value that only *looked* exact on another tab — the routing Target equals the Target Transport Profile — is never remembered: it once made the map say 54/54 and filling stopped one field early (the strict final proof caught it) |
| Mission "1/1 phases done, but the final completion check did not pass" | the exact proof that walks every wizard tab passed after the fill had returned; the exact-state lock was written only before | the lock is written at completion when that proof made the phase exact |
| Chat: "Selected … in Flow Description" while on Configure Source; "structural_opener structural_opener …" | a field that already showed its value kept the chat's field context; internal action tags | the context is cleared; action labels are shown as a person reads them ("BizFlow template link B2B-Flow-PubSub-Template", "+ Add") |

Result: a BizFlow mission on the copy goes + Add → template **link** → Flow Details → **Next** → Configure Source (two flow-identifier rows through the "+") → **Next** → Configure Target(s) (Mapping Transformer and Enricher steps, file-name parts) → **Next** → Configure Routing → routing + Add → the Create Rule drawer (rule, two conditions, action). The final read-only proof shows every tab in turn: **54/54 input.json values exact**, "✅ BizFlow is complete", the deterministic script saved (certified on the next run). Submit, Save, Reset and Delete were never pressed.

## 3. "It needs to learn everything" — the options of each tab

`learn_bizflow_tab_options` reads, without acting:

* every button of the tab and of the wizard's bottom bar, with where it is (tab, column, row, bottom bar, drawer);
* every menu: each column ⋮ (Sort Ascending / Sort Descending / Hide Column), each rule row's Action ⋮ (Edit / View / Delete) — opened, its items read, closed (Escape, or the trigger again only if it is still open). No item is ever chosen;
* commit buttons and commit menu items (Submit, Save, Delete) are recorded as such and never clicked.

The routing tab is read before + Add (no drawer covers the table) and the filled drawer's own buttons (Cancel / Save) after.

How the form is reached and moved through is remembered per phase in `data/hip_memory/phase_navigation/<phase>.json` (`phase_navigation.py`): the entry clicks (`+ Add`, `template link “B2B-Flow-PubSub-Template”`), the tab order, how each tab is left (`Next ›`), its bottom bar, and the options above. The deterministic script uses it:

```
1. Open https://developer.dell.com/hybrid-integrations/bizexchange/bizflows
2. Click “+ Add”
3. Click the template link “B2B-Flow-PubSub-Template” to open the form
4. On the “Flow Details” tab:
5. Type input.json → flow_details.business_flow_name in “Business flow name”
…
   Click “Next ›” at the bottom — the wizard moves to “Configure Source” (it refuses until this tab's required fields are filled)
…
   Learned what “Configure Routing” offers (read-only): buttons + Add, ‹ Previous, Submit; menus Rule Name ⋮ → Sort Ascending, Sort Descending, Hide Column; …; never clicked: Submit
```

## 4. Every phase learns Create, Edit, Clone, Migrate and Deploy

At the end of every mission (`operation_learning.after_mission: true`), `operation_learning.learn_mission_operations` learns, for each phase of the mission, what is not known yet (or older than `refresh_days`):

| Operation | What is learned | Never |
|---|---|---|
| **Create** | the phase's deterministic script (fields, rows, navigation) | — (it is the mission itself) |
| **Edit** | listing → search → row expander → Edit → the whole form (every tab, collapsed section and row), read-only fields, the save label → Cancel | Save / Update / Submit |
| **Clone** | the same, the Clone form | Create / Save |
| **Migrate** | per environment tab, the menu of target environments | a menu item, a confirmation |
| **Deploy** | the menu, confirmation or dialog per environment; an object without Deploy uses Migrate ("through “Migrate” (no Deploy button)") | a confirmation; a guarded Deploy (that may act at once) is opened only with the three-part mutation gate |

When input.json's object is new (not saved yet, so not on the listing), the section is learned on an object the listing already shows (`target_source: first listing row …`). Each learned operation gets a script, `deterministic_scripts/<phase>__<operation>.md`. The chat narrates it (🧭), and the Mission tab's **Operations each phase knows** panel — `GET /api/operation-matrix` — shows the phase × operation table with how each operation is reached, what it opens, and a 📜 button per script.

Measured on the copy (read-only; the server received **no** write request in any run):

| Phase | Edit | Clone | Migrate | Deploy |
|---|---|---|---|---|
| Data Map | 10 fields | 10 fields | DEV → TEST1, TEST2 | confirmation (with the gate) |
| Source / Target Document Type | 13 fields (on an existing document type) | 13 fields | menu | through Migrate |
| Rule | 16 fields | 16 fields | DEV → TEST1, TEST2 | through Migrate |
| Source / Target Transport Profile | 22 fields | 22 fields | DEV → TEST1, TEST2; TEST1 → TEST2 | dialog (with the gate) |
| BizFlow | 23 fields, 4 tabs | 23 fields | DEV → TEST1, TEST2; TEST1 → TEST2 | menu |

Without the gate, a guarded Deploy is reported as "⏸ … a guarded action that may act at once … learned by the first authorized deploy" — never clicked.

## 5. The whole mission: all seven sections, then every operation

One real mission over all seven phases on the portal copy (judges off, no models here), followed by the end-of-mission operation learning:

| Phase | Done at (min) |
|---|---|
| Data Map | 0.7 |
| Source Document Type | 6 |
| Target Document Type | 12.7 |
| Rule | 16.8 |
| Source Transport Profile | 20 |
| Target Transport Profile | 23.3 |
| BizFlow (+ Add → template link → 4 wizard tabs with Next → routing drawer) | 42.5 |

Terminal gate: every phase PASS. Operation learning (13 min): 25 sections learned, **0 write requests**; operations known **32/35** — the 3 open cells are the guarded Deploy buttons of Data Map and the two Transport Profiles, which are opened only with the mutation gate (with the gate, a separate run learned them: the Data Map confirmation and the Transport Profile dialog, never confirmed, still 0 write requests).

That run also showed the last blocker of a whole mission: the final consolidation counted a phase verified `pass_with_warnings` as failed. The Transport Profiles' only warning is about learning evidence ("Many dropdown controls have no captured/enriched options") on forms proven exact; the section judge and the mission trace already treat it as a pass, and now the consolidation does too (a `failed` verification still blocks). Re-evaluated on that run's own artifacts: **application complete**.

## 6. Configuration

```yaml
operation_learning:
  enabled: true
  after_mission: true
  actions: [edit, clone, migrate, deploy]
  refresh_days: 7
  max_seconds: 1800
```

`FullDummyFillOptions.learn_operations` (None = the config decides). No other setting changed.

## 7. Tests

`tests/test_v243r39_bizflow_wizard_and_operations.py` (17), real Chromium:

* the copy's + Add shows the template card, its link opens the wizard, tabs ahead are locked;
* the agent follows the template **link** and remembers it;
* Next refuses an empty tab and names the missing fields; filled, it moves to Configure Source; Previous goes back;
* a locked tab is reached with Next; a refused Next raises `HIP_BIZFLOW_NEXT_BLOCKED` with the missing fields; a reached tab opens from its header;
* the row "+" finder never takes a tab header or the wizard bar;
* the routing tab's options: 5 column menus, the row Action menu (Delete marked as a commit), Submit recorded, nothing chosen, every menu closed;
* navigation knowledge → the script says template link, Next per tab, options;
* the operation matrix (Rule Deploy "through Migrate");
* `GET /api/operation-matrix`; the Control Center panel; config and mission wiring; the chat field context;
* a real BrowserSession learns BizFlow and Source Document Type Edit / Clone / Migrate / Deploy on the copy with **zero** write requests (Document Type on an existing row).
* the wizard memory counts only values read on their own tab (the routing Target / Target Transport Profile case).
* a wizard phase's script joins every tab's skill; a row's own "Create Condition" / "Remove" is not a commit;
* the final consolidation accepts `pass_with_warnings` and still blocks `failed`.

The R38 mission test keeps `learn_operations=False` (it covers the Data Map mission only).
