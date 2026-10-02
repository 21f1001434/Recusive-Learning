# V243R36: a filled form finishes, and the agent understands you (2026-10-02)

## What was reported

On a live Source Document Type run, the agent filled the form and the chat said every field was verified ("Status = Enabled — verified"), but the agent kept going:

- The live map stayed at **0/29**.
- The proof said **"Live form not yet exact: status"**.
- The no-progress watchdog stopped the attempt.
- The recovery ladder then threw the filled form away (refresh, reopen, browser restart) and filled it again, up to attempt 5.
- It ended with three errors:
  - "target center is intercepted";
  - "Executor reconnect blocked: HIP_RECONNECT_CDP_ENDPOINT_UNHEALTHY";
  - blocked by the section judge.
- Typing "everything filled correctly" in the chat was not understood. The agent kept replicating the form.
- The chat was noisy, with lines like "Semantic target SC-… approved for select".

You also asked why gpt-oss-20b wins the model qualification over gpt-oss-120b: isn't bigger better?

## Root causes

| What you saw | Why |
|---|---|
| Live map frozen at 0/29; "stop when complete" never fired | The read-only probe treated the form as "busy" while any menu or list was open. The portal's own navigation menu (`role=menu`) is always on screen, so the form was "busy" forever: the map was never computed and the stop-when-complete check never ran. |
| "Status = Enabled — verified" but "not yet exact: status" | The live Status switch is a `<button role="switch" aria-checked="true">` with no value text. The executor reads a switch by its checked state (so it verified it). The proof's judge compared value text, found none, and reported Status missing. |
| The form was reopened blank and filled again | A watchdog stop on a form that was exact except for a value the judge could not read went to the recovery ladder. Each step (refresh, reopen, browser restart) discards the filled form. Accepting the review without an exact proof became "recheck", and the retry reopened a fresh form. |
| "Everything filled correctly" was ignored | The chat only knew a few fixed words, and a message mentioning the **Status** field ("status is wrong") was read as a request for the agent's status. Nothing let the operator confirm a phase. |
| "target center is intercepted" | A popup left open by an earlier field, or a toast, still covered the next field after the browser restart. The agent gave up instead of closing or waiting it out. |
| HIP_RECONNECT_CDP_ENDPOINT_UNHEALTHY | After the browser restart, the old Chrome still held the fixed DevTools port 9237. The new Chrome had no DevTools endpoint, and every reconnect failed. |
| gpt-oss-20b beats gpt-oss-120b | Qualification ranks by accuracy on the live page. **On a tie, latency decided**, so whenever both models answered the same questions correctly, the faster gpt-oss-20b won. A single timeout or an answer only in the reasoning channel also counted as zero for the slower 120b, with no second ask. |

## What changed

### The proof reads the form the way the executor does

- **Switches and checkboxes are judged by their checked state.** "Enabled", "Yes", "On" and "True" mean checked; "Disabled", "No", "Off" and "False" mean unchecked. A radio is never read as a switch.
- **The executor's own reader cross-checks the proof.** A value the judge cannot pair with a form field is checked again with the executor's control resolver and equality, the same reader that filled and verified it. For example, a DDS dropdown whose inner input goes blank once its menu closes still shows its option as a chip: the executor reads the chip, while the judge read the blank input. A value equal there is matched and listed as `executor_reader_matched_fields`; otherwise it stays missing.

### The live map counts while the agent works

- **Only a popup that is open makes the form "busy":**
  - a combobox or popup trigger expanded right now, with a list it owns;
  - a visible loader.

  The portal's navigation, header and sidebar menus, a chip list and an open accordion no longer count.
- **A busy signal that never ends is read through after 20 s.** Reading touches nothing.

### You can confirm a phase

Type "everything is filled correctly" (or "all fields are correct", "the form is complete", "looks good, move on", "accept"), or click **Accept** in the chat or the Phase Review panel:

1. The agent **stops filling**: the watchdog stops the attempt at its next sample, and the chat says "🛑 You confirmed the form — I stop filling and finish this phase".
2. It **proves the form read-only**. Values it can read back are proven by itself. Values it cannot read back are recorded as **confirmed by you** (`operator_confirmed_fields`), and the chat names them.
3. It **finishes the phase on the form on screen**. A retry first proves the form already on screen. When that form holds every value, it is kept: no route, no reopened form, no refill.

A confirmation never covers:
- a field the portal itself flags invalid;
- a form that is not on screen;
- more than three values the agent cannot read back (`agent_chat.operator_confirmation_max_unread: 3`).

In each case the agent says why in the chat and keeps working. A confirmation typed while the form is still being filled is not lost: once only values the agent can't read back remain, it finishes the phase. A confirmation never saves, submits or deploys anything.

### A nearly complete form is not thrown away

A stall (no-progress watchdog or wall budget) on a form that holds every input.json value but one or two the agent cannot read back now **asks you** instead of refreshing, reopening or restarting the browser:

> ❓ The Source Document Type form holds 28 of 29 input.json values, but I can't read back status. Is the form correct? Accept: I finish the phase as it is (nothing is saved on the portal). Needs correction: fix it on screen, then I check it again. Either way I keep this form open instead of reopening it blank.

- The question appears in the chat banner with **Accept** and **Needs correction**.
- **Accept** finishes the phase on that form.
- **Needs correction**: you fix the form on screen, then the agent proves it again.

A broken page still goes straight to recovery, as before:
- a loader that never clears;
- an expired login;
- a Whitelabel error page;
- a lost browser;
- another page.

Setting: `runtime_self_heal.ask_before_reopening_max_missing: 2` (0 turns it off).

### The chat understands you in context

| You type | Understood as |
|---|---|
| "everything filled correctly", "the form is complete", "looks good, move to the next phase" | **Accept**: confirm the current phase. |
| "status" / "status?" / "what are you doing?" | The agent's status. |
| "Status is enabled", "status is wrong" | A **hint** about the Status *field*. It is a **rejection** while a review waits. |
| "yes" / "go ahead" | Accept, while a review waits; otherwise resume, while paused. |
| "no" | Reject, while a review waits. |
| "is everything filled correctly?" | A question, never a confirmation. A negation is never a confirmation either. |

When the patterns read a message only as a hint, the configured Dell AIA model may classify it (`agent_chat.model_intent_fallback: true`). The model's answer is used only when its confidence is at least 0.8, and "stop" is never taken from it.

### Quieter, clearer chat

- Semantic-gate approvals ("Semantic target SC-… approved") no longer appear in the chat.
- The banner shows the agent's question in plain words, instead of "(automated: blocked)".

### Clicks and the browser recover by themselves

- **A covered click target is cleared, when safe:**
  - a popup left open by an earlier field (list, menu, tooltip, popover) is closed with Escape, which commits nothing;
  - a toast, alert, backdrop or loading layer is waited out;
  - a dialog is never dismissed.
- **A browser restart never reuses a held DevTools port.** It waits up to 10 s for the old Chrome to release the port, then relaunches on a free one. The CDP probe also follows the port Chrome reports in its profile's `DevToolsActivePort`. The executor reconnect gets its full time budget.

### Model qualification: a tie goes to the more capable model

- **Accuracy on the live page still decides.** A model that answers more questions correctly wins, whatever its size.
- **On equal accuracy, the more capable model wins.** gpt-oss-120b is ahead of gpt-oss-20b. Latency decides only between equally capable models.
- **A failed answer gets a second ask.** An error, a timeout or an answer that is not JSON is asked again once (`model_portfolio.qualification_retries: 1`).
- **A reasoning-only answer is read.** When a reasoning model's final text comes back empty, its answer is read from the reasoning channel.
- **Every model's result is explained.** For example: "same score (12/12); the champion is the more capable model". The explanation appears in the CLI table (`qualify-models --show`) and in the Model Champion tile's tooltip.
- **Your lock is re-validated once.** The qualification version is now 3, so the lock you have (gpt-oss-20b) is re-checked at the next certification or live mission: every model is asked again and the champion is chosen under the new rule.

## Proof

Tests in `tests/test_v243r36_operator_confirmation_and_live_doctype.py`:

- **The live-like Document Type** (Status as a value-less `<button role=switch>`, a permanent navigation menu):
  - the probe reads "filling" before the fill, not "busy";
  - the engine fills it in **one** cycle;
  - the probe then reads "complete" and the read-only proof passes with Status matched.
- **Busy detection:**
  - shell menus, an open accordion and a chip list are not busy;
  - an open dropdown is busy;
  - a busy signal that never ends is read through.
- **Switches:** on matches "Enabled" by `checked_state`, off does not, and a radio is never a switch.
- **Executor cross-check:** a DDS dropdown whose inner input went blank after its menu closed (the chip still shows the committed option) is proven by the executor's reader. A chip with another value is not.
- **Operator confirmation:**
  - only during this mission;
  - completes the proof with `operator_confirmed_fields` and says so once;
  - refused for a field the portal flags, a form that is not on screen, or four values still missing (the agent names them and keeps filling).
- **The watchdog** stops on a confirmation, and keeps working when the confirmation cannot complete the form.
- **Kept form:** an attempt that keeps an exact form needs no route and no refill.
- **Near-complete question:**
  - asked for a stall with one unreadable value;
  - not asked for a broken page, three missing values, an invalid field, a proof from another attempt, or no form on screen;
  - the mission asks it before the recovery ladder.
- **The chat in context:** confirmations, hints about the Status field, reject while a review waits, yes/no by context, and the bounded model fallback.
- **The Control Center:** chat Accept and Phase Review Accept both confirm the phase, the question shows in the banner, and a rejection confirms nothing.
- **Clicks:** a leftover popup is closed with Escape, a toast is waited out, and a dialog is left alone.
- **CDP:** the endpoint is rediscovered from `DevToolsActivePort`, and a held port is replaced on relaunch.
- **Qualification:**
  - a tie goes to gpt-oss-120b;
  - a timed-out answer is asked again;
  - a reasoning-only answer is read;
  - a more accurate model still wins over a bigger one.

Two earlier tests were updated for the deliberate changes:
- **R24:** "a faster model wins a tie" became "a tie goes to the more capable model", and a failed model is asked twice.
- **R35:** "accept" with no review waiting now confirms the current phase.
