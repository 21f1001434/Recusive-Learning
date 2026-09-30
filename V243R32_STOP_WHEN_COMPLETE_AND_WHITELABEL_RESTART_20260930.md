# V243R32: stop filling once the form is complete; a Whitelabel Error restarts the stage (2026-09-30)

## What was asked

- "It filled correctly but it didn't know when to stop. It kept on filling and filling and handling errors. After filling everything it should understand and stop filling."
- "There can also be an issue called Whitelabel Error. If that occurs, close the browser, reopen it and start from the stage."

## Why it kept filling (reproduced before the fix)

Four separate things kept a correctly filled form being filled again.

| # | Where | Before R32 |
|---|---|---|
| 1 | **Branch exploration after the fill** (Transport Profile, Business Flow tabs and routing, Rule, Data Map) | After a correct fill, every parent dropdown was switched to each of its other values (up to 24 parents × 30 values) to "explore" branches. Then the whole form was filled again to restore it, and re-judged. A restore that failed raised an error, which triggered more recovery and another fill. |
| 2 | **Goal engine cycles** | Filling a field that was already proven counted as progress. A form whose own checks never all held (for example a portal-required field that input.json does not name) was filled again on **every** cycle. On the Transport Profile replica that was 4 of 4 cycles, then "needs_input". The mission then reopened the form and filled it again. |
| 3 | **The "is it complete?" proof (R29)** | The proof could not read the Transport Profile, Business Flow, Rule or Data Map forms. It expected each field under the form title ("Create Transport Profile"), but the portal puts fields under their fieldset ("Basic Details :"). It also dropped a checked "No" radio. On a complete Transport Profile it matched 1 field of 14, so it never said "complete". |
| 4 | **Watchdog and phase budget** | Every refill looked like progress, because it produced new screens, new executor heartbeats and more fills. The watchdog never stopped a refill loop, and the budget kept granting more time. |

A Whitelabel Error Page (Spring Boot's "This application has no explicit mapping for /error … status=500") was not recognised at all. It surfaced as "field not found" or "page context destroyed" and was retried field by field.

## What the agent does now

### 1. It stops once every input.json value is filled

**The form is left alone once it is complete.**
- Branch exploration after the fill is now read-only by default. The other branches are recorded from what the capture already read; no dropdown is opened and no value is changed.
- Because nothing on the form changed, it is not filled or judged a second time (`learning_order`: "target branch filled once; … form not refilled").
- The old behaviour is opt-in: `exploration.explore_branches_after_fill: true`, or `HIP_EXPLORE_BRANCHES_AFTER_FILL=true`.

**The goal engine proves the form, then stops.**
- After any cycle whose checks were not all met, the live form is proved read-only against input.json.
- When every value is filled and committed, the goal is achieved:
  - `completed_by: input_json_exact_on_live_form`, `stopped_filling: true`;
  - `verified_by: input_json_live_read_only_proof`;
  - the executor's own flags are kept (`executor_pass`, `executor_exact_execution_verified`).
- A skill is saved only from a run whose own checks all held.
- Progress now means a field proven for the first time, or a form shape never seen before. Refills are not progress, so two idle cycles end the run.
- Setting: `autonomous_form.stop_when_input_json_exact: true`.

**The proof reads every phase's form.**
- Form-level facts are matched on the whole active form, still by label and by the exact committed value.
- Row facts keep their strict section match.
- A radio group answers with its checked option.
- On the replicas every field is now matched: Transport Profile 14/14, Rule 19/19, Data Map 6/6.

**The mission's pre-judge gate proves the live form first.** It used to reopen and refill whenever the executor's own record was incomplete.

**The watchdog knows what "complete" looks like.**
- Progress units count distinct fields verified, filled or clicked. A refill adds nothing.
- After 120 s with no newly verified field, the live form is probed read-only. The probe never opens, closes or blurs anything, and answers "busy" while a dropdown is open or the portal is loading.
- Two exact probes in a row stop the attempt as complete (`HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL`). The mission then goes on to the judges without reopening the form.
- 600 s of filling with no new field is a refill loop. It is stopped: if the form is exact it is complete, otherwise the recovery ladder takes over.
- Settings: `runtime_self_heal.refill_probe_seconds` and `refill_loop_seconds`.

### 2. Whitelabel Error Page: close the browser, reopen it, start the stage again

**Detection.** The page's own words are checked in the page and its frames: "Whitelabel Error Page" and "no explicit mapping for /error". The status and type are read (for example 500, Internal Server Error). It is checked in five places:
- the progress marker, so the running attempt ends within one poll;
- every form-surface check (each goal-engine cycle);
- navigation to a phase link;
- the recovery step itself;
- operations navigation.

It is also checked when any other error ended the attempt, because a destroyed page context is how the error page usually shows up.

**Recovery (new class `whitelabel_error_page`).** The browser is closed and reopened. Then the same phase link is opened, the form is opened again and filled from input.json. The stage starts again; completed phases stay complete.
- Up to 3 browser restarts per phase (`runtime_self_heal.whitelabel_browser_restarts`).
- After that the phase is held for a human with `HIP_WHITELABEL_ERROR_AFTER_RECOVERY`. The browser stays open, and Resume grants a fresh ladder.

**Safety.**
- The error page is never taken for a finished form: no "complete" shortcut and no read-only proof.
- A mutation outcome stays a mutation question: `unsafe_or_mutating` wins.
- Operations (Edit / Clone / Deploy / Migrate) restart the browser and perform the operation again from its listing only when nothing was saved yet.
- If the error page follows a Save, Submit, Deploy or confirm click, the operation is not repeated (`whitelabel_after_commit`), so there is never a double write.

## Proof

Tests in `tests/test_v243r32_stop_when_complete_and_whitelabel.py` (14). New support: `tests/whitelabel_portal_support.py`, a replica portal whose page turns into Spring's Whitelabel Error Page mid-fill, or whose phase link answers with it.

- **Complete form, engine checks unmet** (Transport Profile replica, with one required field that input.json does not name):
  - before R32: 4 of 4 cycles, "needs_input";
  - now: 1 cycle, pass by the read-only proof (14/14 fields);
  - with the stop switched off, refills are not progress: 3 cycles, not 4.
- **Whitelabel**, a real browser against the replica portal:
  1. Mid-fill the page becomes the error page. The watchdog sees it and the browser is closed and reopened (session 2).
  2. The same link answers with the error page. The form check sees it and the browser is restarted again (session 3).
  3. The stage is opened from its link, the form filled, and the phase passes. The ladder learns that the restart resolved it.
- **Persistent Whitelabel:** 3 restarts, each to the same stage link, then held for a human.
- **Operations:** restarted and repeated before a Save; never repeated after one.
- **Watchdog:**
  - a complete form stops within two probes;
  - a refill loop on an incomplete form goes to recovery;
  - new fields keep an attempt running;
  - a Whitelabel marker ends the attempt at once.

## Settings (all have defaults; a kept config.yaml needs no change)

```yaml
exploration:
  explore_branches_after_fill: false      # true = switch parents to other values after the fill, then refill
autonomous_form:
  stop_when_input_json_exact: true
runtime_self_heal:
  refill_probe_seconds: 120
  refill_loop_seconds: 600
  whitelabel_browser_restarts: 3
```
