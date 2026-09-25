# V243R16: Document Type is no longer cancelled mid-form, and a finished phase shows its verdict (2026-09-25)

## Symptoms (live run)

- **Source Document Type** filled Name, Transaction Type, Data Format Type and Description, then stopped at **Document Identifier**. The learning-phase review said:

  > HIP_PHASE_NO_PROGRESS_WATCHDOG: The live HIP phase produced no new structural browser state within the bounded interval; cancel this attempt and enter deterministic recovery...

  Each new attempt reopened the form ("click Add Document Type", attempt 4) and stopped in the same place.
- **Data Map** was complete and handed off to P02, yet its Verification read **"Pending • judge pass"**.

## Root causes

### 1. The no-progress watchdog killed a fill that was still advancing

`phase_progress.run_with_progress_watchdog` counted only a new DOM-state signature as progress, and allowed 90 s without one. Two things produce long stretches of already-seen states while the agent is working:

- **Retrying a DDS dropdown** (open, wait for options, close) returns to states the page has already shown.
- **Model decisions** around every action change nothing on screen. One dropdown involves several AutoWebGLM and semantic-gate calls, and the Dell AIA timeout is 90 s.

So a field that needed retries at Document Identifier looked like a stall. The whole attempt was cancelled and restarted from a fresh form, which hit the same field again.

**Reproduction.** The real watchdog wrapped the Document Type replica, with 3 s of simulated latency per model decision and late Operation options:

| | Result |
|---|---|
| Before | `HIP_PHASE_NO_PROGRESS_WATCHDOG` after 108 s. Filled: Name, Transaction Type, Data Format Type, Description. Nothing from Document Identifier onwards. |
| After | `goal_achieved`. All 15 fields filled, including Operation, Derived From, Value, both attributes and Validation Type. |

### 2. The verification verdict was read from a key that does not exist

`build_phase_verification` reports its verdict as `status` ("pass", "pass_with_warnings" or "failed") and has no `pass` key. The mission trace read `verification.get("pass")`, got nothing, and the card showed "Pending".

## Fixes

- **Executor heartbeat.** Both form executors (`execute_document_type_state_graph` and `execute_phase_state_graph`) publish `publish_executor_progress` for every field start, attempt, retry and completion. `BrowserSession.capture_phase_progress_marker` carries the token.
  - The watchdog now counts a new heartbeat or a new successful fill as progress.
  - The number of heartbeat units is bounded by fields × retries × cycles.
  - A genuinely stuck operation still trips the watchdog: the same state and no new work, such as a hung call or UI cycling with no field progress. The watchdog evidence now records `executor_progress_units` and `last_executor_progress`.
- **Per-field time budget.** `node_time_budget_ms` defaults to 75 s. A field that does not commit within its budget is left to the repair pass (`HIP_NODE_TIME_BUDGET_EXCEEDED`) instead of holding the form.
- **Verification verdict.** `mission_trace.verification_verdict` reads an explicit `pass` if there is one, otherwise the `status`, with the exact-completion checkpoint as a fallback. The Control Center shows "Exact pass", or "Pass (warnings)" for a pass with non-fatal warnings.

## Tests

`tests/test_v243r16_watchdog_and_verification.py`:

- A heartbeat counts as progress, while A→B→A cycling with no new work still trips the watchdog.
- New fills count as progress.
- The verdict mapping.
- A completed phase card shows its verdict.
- The Document Type replica completes under the real watchdog with slow model decisions.

## Verification

| Check | Result |
|---|---|
| Full suite (185 files) | 1,346 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. The packaged web UI copy (`backend/webui/app.js`) is kept identical to `webui/app.js`. |
| 7-phase local mission UAT (`certify-final-mission`) | `pass: true`; all 7 phases pass |
| `VERIFY_V243R16_INSTALL.ps1` R16 smoke checks | All pass |

## Apply

```powershell
.\APPLY_V243R16_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R16_INSTALL.ps1
```

Restart the Control Center and start a new mission. R16 includes R13, R14 and R15.

## Validation boundary

The mechanism was reproduced on the replica with simulated model latency. The live Document Identifier field's own reason for needing retries is not visible in the screenshots. If a field still fails live, the rest of the form is now filled, the repair pass retries it, and the review names that field.

After a run, `source_document_type/phase_no_progress_watchdog.json` (if present) and `doctype_kb/doctype_autonomous_form_execution.json` show which field it was.
