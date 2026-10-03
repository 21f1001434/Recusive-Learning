# V243R38: whole missions keep going, and say what they learned (2026-10-03)

## What was reported

1. "I triggered only Data Maps. It completed, but I'm not sure whether the deterministic script was generated."
2. "Source Document Type completed, but Target Document Type keeps failing, even though in some of the 14 attempts it filled every value."
   - The screenshot shows attempt 15.
   - The listing is on screen, the live map is at 0/29, and the message is "Live form not yet exact: document_type_name, transaction_type, data_format_type, document_type_version, status".
3. "Run on its own, a section has a chance of completing. With all sections, after one or two phases it gets stuck, loading forever, and doesn't recover."
   - The screenshot shows "route handoff needs bounded recovery: HIP_PHASE_HANDOFF_FAILED".
   - It also shows "Navigation failed after retries: … neither target HIP nor Dell SSO was usable".

## How it was reproduced

Every earlier replica test drove a single form. The mission itself had never run end to end outside the live portal:
- the phase sequence;
- the handoffs between phases;
- self-heal;
- the human hold.

`tests/hip_portal_sim.py` is a local copy of the portal.

| Part | How it works |
|---|---|
| Addresses | Chromium resolves `developer.dell.com` to a local HTTPS server (`--host-resolver-rules`). Every URL, the route checks and the handoffs see the real portal addresses. |
| Modules | Each module is served at its real path (Data Maps, Document Types, Rules, Transport Profiles, Business Flows). Each has the portal's header, a menu naming every module, and a listing with **+ Add**. |
| The Create form | **+ Add** opens the module's full replica form as the portal does: <ul><li>a modal drawer over the listing for Data Map, Document Type and Transport Profile;</li><li>a form page for Rule and BizFlow.</li></ul> Rows are added with the live legend "+". **Cancel** discards the form. |
| Faults | Each can be set per module and page load: <ul><li>an endless spinner;</li><li>a bounce to the portal home;</li><li>a blank page.</li></ul> |

The real `FullDummyFillE2EFlow` (the code a Control Center mission runs) was then run against it, one section and all seven.

## What was wrong, and what changed

### 1. Every structural click waited 45 seconds after it had worked (Target Document Type, attempt 15)

**What happened**
- After each click, the form-memory observer looked the clicked element up again by its *selector hint*.
- For **+ Add** that hint is a label ("Add Document Type"), and once the form opens there is nothing to match. Playwright waited its full page timeout: **45 s**.
- The Add retry then clicked the vanished button and waited another 45 s.
- With 90 s and no visible progress, the no-progress watchdog cancelled the attempt and reopened the form.
- On the listing, the live check reads 0 values, hence the screenshot: "Live form not yet exact: document_type_name, …".

**Fix**
- The observer never waits for an element. It checks `count()` and reads with a 1.5 s limit.
- The Document Type Add routine polls up to 6 s for the drawer after a click.
- It never retries a button that is gone; it finds **+ Add** again or stops.

**Result:** the click on the simulator takes about 2 s, down from 46 s.

### 2. The Document Type and Rule row adders failed the attempt instead of letting the goal engine add rows

**What happened**
- The listing-era row adders only know text buttons.
- On the live portal, attribute rows and Rule conditions are added with an icon-only "+" in the section legend. Since R20 the goal engine's structure healer finds and clicks it while it fills.
- The Document Type deterministic-first path ("could not create the required repeatable rows") and the Rule conditions transaction ("refusing row reuse") raised before the goal engine ran.
- Every attempt therefore ended in "reopen the form".

**Fix**
- A shortfall is recorded and handed to the goal engine.
- The goal engine binds every row by its own index, so no row is reused.
- The exact input.json proof still decides whether the phase is complete.

### 3. Target Document Type typed over Source's form

**What happened**
- Source and Target Document Type share one URL.
- If the portal's Cancel did not close the Source drawer, the handoff saw "already on the right page" and skipped navigation.
- Target's **+ Add** was then blocked by the open drawer, and Target filled Source's form:
  - leftover rows survive;
  - with Save after fill, the wrong form would be the one on screen.

**Fix**
- At a phase boundary the agent now checks that the previous Create form really closed.
- If it didn't, the page is reloaded, which discards the unsaved form (nothing is saved here).
- On the simulator, Target now starts at 0/29 on a clean listing and opens its own form.

### 4. A page that never finished loading looked usable, and a stuck one was never recovered

**What happened**
- The route check looked for the module's name in the page text. The portal menu names every module, so a module stuck on its spinner passed.
- When navigation did fail, the only remedy was the same `goto` again.
- Self-heal treated it as a transient timeout and re-navigated, failing the same way.

**Fix: a module that never loads is detected.** A visible spinner with no module content outside the header and menu is reported as "module still loading", not usable.

**Fix: an in-place recovery ladder inside navigation.** These steps keep the same tab and sign-in, so the phase's page stays valid:

| Step | What it does |
|---|---|
| Wait | Waits a little longer. |
| Reload | Reloads the page. |
| Portal menu | Opens the module from the portal's own menu (single-page navigation, from the portal home if needed). |
| Fresh document | Clears session storage, opens a blank page, then the module. |

- Each step gets `route_recovery_step_seconds` (25 s) and is narrated in the chat.
- The step that resolved each module is remembered (`data/hip_memory/route_recovery_ladder.json`) and tried first next time. A step that never helped goes last.

**Fix: escalation**
- **At a handoff** (where no form flow holds the page), a module still stuck after the ladder gets one browser restart. The same profile is used, so the Dell sign-in is kept.
- **Inside a phase**, the error is `HIP_ROUTE_STUCK_LOADING`. Self-heal treats it as a stuck portal loader: refresh, then a browser restart (learned order). Before, it re-navigated.

### 5. All sections: a phase that needed you held the whole mission for ever

**What happened**
- With `human_in_the_loop.incomplete_phase_wait_seconds: 0`, a phase that could not recover kept the browser open and waited for an operator indefinitely.
- In an all-sections mission, every later phase waited too ("⛔ Blocked" in the tab title).

**Fix:** with more phases still to run:
- the phase asks you and waits `incomplete_phase_wait_seconds_when_more_phases` (900 s);
- then the mission carries on with the remaining phases;
- it comes back to that phase once at the end, with a fresh browser (`deferred_phase_retries: 1`).

The chat says each step:
- "⏸ Rule needs you … I'll wait up to 15 min";
- "⏭ No answer for Rule — continuing with Source Transport Profile";
- "↩ Back to Rule (deferred earlier): fresh browser, new attempt".

The final verdict still needs every phase complete.

### 6. With the section judges off, a completed mission was reported blocked

**What happened**
- The terminal gate requires a `section_judge_gate.json`, which was written only when a judge ran.
- The chat then said "1/1 phases done; see the blocked phase above".

**Fix**
- With the judges off, the exact input.json proof is recorded as the gate.
- If every phase finished but the final check still fails, the chat says exactly that.

### 7. The Rule form from the golden screenshots was not recognised

**What happened:** the Rule detector needed "Rule Type Name", "Rule Identifier", "Root Element" or a `ruleName` attribute.

**Fix:** a "Create Rule" form with at least three of its own sections and fields is now recognised too:
- Document Type Name (Version);
- Rule Type;
- Rule Scope;
- Execute Action(s) When;
- Conditions;
- Actions.

### 8. An exact Rule form was reopened three times, and a completed mission ended "failed"

**What happened**
- The Rule form held every input.json value: the live check showed 19/19, and the chat said "Every input.json value is filled and committed on the live form".
- Yet the completion gate blocked it. A coverage report built from the old listing-era pass still counted a step that had failed: typing into the Status checkbox.
- When a phase did complete that way, its verification record still said "failed", and the final consolidation reported the whole mission as failed.

**Fix (the R29 rule: the live form is the answer)**
- When the exact input.json proof passes, a disagreeing coverage report is a learning warning, not a block.
- The phase's verification record says it completed on the exact proof.
- Each attempt uses only its own proof.
- The Rule flow's old generic pass no longer types into switches, checkboxes, radios or read-only fields; those were failing and alarming the chat.

### 9. Smaller things seen during the runs

- The chat attributed the next phase's clicks to the previous phase's last field ("Clicked “Condition Type” for Validation Type"). The field context is now cleared per phase and after each field.
- The "learning / replaying the script" line is said once per phase, not once per BizFlow tab.
- After a phase completed, the chat header could still show its last mid-fill count ("Rule 16/19"). The live map now takes one last reading at completion.
- R27's route loop already waits for a slow module and reloads it once. Inside that loop, the new ladder stays out of the way.
- If the route loop ends with "module not rendered", self-heal now treats it as a stuck loader (refresh, then restart) instead of routing again.

## The deterministic script: generated, where, and certified

**What it is**
- What a completed phase teaches the agent is a *skill*: for every input.json path, how the form field is found and filled.
- It is value-free: no values, selectors or screen positions are stored.
- The next run replays it without models and checks every value. A replay that reproduces the fill exactly **certifies** it.
- It always existed, but only as internal bindings, so nobody could see it. The dashboard's "1 learning" was a Data Map skill waiting for that replay.

**Now, after every completed phase:**

| Where | What you get |
|---|---|
| Run folder | `runs/<run>/<phase>/deterministic_script.md` and `.json`: the script of this run, step by step. |
| Memory | `data/hip_memory/deterministic_scripts/<phase>.md` and `.json`: the phase's current script, plus `index.json`. |

The script's steps:
1. Open the page.
2. Click **+ Add**.
3. Add rows until there is one per input.json item.
4. For each field, its action, field (and row) and input.json path.
5. Read every value back.
6. Stop: nothing is saved.

**The chat says:**
- before the fill:
  - "📜 Replaying the deterministic script for this form (certified) — no models", or
  - "📜 No certified deterministic script for this form yet — learning it now";
- after the phase: "📜 Deterministic script for Data Map saved (candidate): 10 steps (6 fields) — the next run replays it without models and certifies it when every value matches. File: …".

**In the Control Center**
- The **Mission** tab has a **Deterministic scripts** panel: phase, status (candidate / certified / stale), steps, replays and updated time.
- **View** shows the script.
- The Induced Skills tile counts the scripts.
- API: `GET /api/deterministic-scripts`.

## Proof

### Simulator, single sections (the real mission code)

| Run | Before | After |
|---|---|---|
| Source Document Type | Every attempt: "+ Add" held 46 s, watchdog, form reopened. Then "could not create the required repeatable rows". Blocked. | Complete in one attempt, 29/29 proven. Final gate passes. |
| Target Document Type after Source (learned memory reused) | — | Complete, 29/29. "+ Add row" through the live "+" four times. |
| Data Map | — | Complete, 7/7. Deterministic script saved (candidate, 10 steps). Mission complete. |

### Simulator, all seven sections

**One mission, all seven sections, fresh memory, on the latest code.** Faults were injected:
- Rules spun for 2 loads at its handoff (navigation's own retries absorbed it);
- Transport Profiles bounced to the portal home.

| Phase | Result | Attempts | Time |
|---|---|---|---|
| Data Map | ✅ complete, 7/7, script saved | 1 | 0:27 |
| Source Document Type | ✅ complete, 29/29, script saved | 1 | 5:23 |
| Target Document Type | ✅ complete, 29/29; started on a clean listing (0/29) and opened its own form | 1 | 6:24 |
| Rule | ✅ complete, 19/19; condition row 2 added through the live "+" | 1 | 4:09 |
| Source Transport Profile | ✅ complete, 14/14 | 1 | 3:12 |
| Target Transport Profile | ✅ complete, 14/14 | 1 | 3:22 |
| BizFlow | not yet on the simulator | — | — |

**About BizFlow:** its flow stops at the wizard's "Source Details" step. The BizFlow replica's tabs do not drive the BizFlow flow's tab model inside a mission. BizFlow's own replica tests (the goal engine on the full wizard) still pass. This is a simulator gap to close next; it is not one of the reported issues.

**Before R38 the same mission stopped at Source Document Type:**
- every "+ Add" was held for 46 s;
- the attempt was then cancelled and the form reopened;
- in the end the phase was blocked by the row adder.

**Recovery and continuity, each in a real mission:**

| Situation | What the agent did |
|---|---|
| Rules spinning for 5 loads at the handoff | "⏳ The Rules page did not load (module loading); recovering it step by step …", then reload, then portal menu, then fresh document: "✓ The Rules page loaded after loading it in a fresh document; continuing". The phase completed and the mission finished: "🏁 Mission complete — 2/2 phases done" (application complete, terminal gate pass). |
| Transport Profiles bouncing to the portal home for 4 loads | Recovered the same way, then completed 14/14. |
| A phase the agent could not finish, with more phases to run | "⏸ Rule needs you … I'll wait up to 15 min", then 15 minutes later "⏭ No answer for Rule — continuing with Source Transport Profile; I'll come back to Rule at the end". The mission went on with the next phases instead of holding the browser. |

### Tests: `tests/test_v243r38_mission_continuity.py` (16)

Real browser and real `BrowserSession` against the simulator, plus one full mission:

| Area | What is checked |
|---|---|
| The 45 s click | The observer returns in under 3 s, down from 45 s. |
| + Add | The Add routine waits for the drawer and doesn't retry a vanished button. |
| Row shortfall | Handed to the goal engine, not raised. |
| Rule form | The golden-screenshot form is recognised; the listing is not. |
| Same-URL handoff | A Create form left open at a phase boundary is discarded before the next phase. |
| Endless spinner | Reported "module still loading" (although the menu names the module), recovered in place, and the resolving step is remembered. |
| Bounce to the portal home | Recovered. |
| Ladder order | The learned order puts the step that worked first. |
| Handoff, stuck for good | One browser restart, then the handoff passes. |
| Self-heal | `HIP_ROUTE_STUCK_LOADING` is a stuck loader for self-heal (refresh, then restart). |
| Bounded hold | With more phases to run, the hold ends after the bounded wait, the phase is deferred, and the chat says so. |
| Final message | When all phases finished but the final check failed, the message says that. |
| Exact proof | An exact live form is not blocked by a disagreeing coverage report, and each attempt starts without an earlier proof. |
| Deterministic script | Readable and value-free; the listing follows certification; the API and both Control Center copies show it. |
| Full mission | A Data Map mission completes, the final gate passes with the judges off, the chat reports the script, and the live map ends on the completed form. |

## Settings (all have defaults; a kept `config.yaml` needs no change)

```yaml
runtime_self_heal:
  route_recovery_enabled: true
  route_recovery_step_seconds: 25
  handoff_restart_browser_on_stuck_route: true
human_in_the_loop:
  incomplete_phase_wait_seconds_when_more_phases: 900   # 0 = hold indefinitely, as before
  deferred_phase_retries: 1
```
