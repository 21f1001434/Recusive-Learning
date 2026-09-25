# V243R18: A stuck portal spinner is fixed automatically (refresh, then browser restart) and the agent learns which step works (2026-09-25)

## What was reported

On **Source Document Type**, after Name and Transaction Type, the Dell portal kept its loading spinner up:
- The step card showed "safe page refresh: vision-confirmed blocking loading persisted for 300.1s … attempt 2".
- The phase then stopped for human review with `HIP_PHASE_NO_PROGRESS_WATCHDOG` ("Automated: BLOCKED").

The required behaviour:
1. If the spinner stays longer than the allowed time, refresh the page.
2. If it is still there, close the browser and open it again.
3. Continue by itself.

The model in use is **gpt-oss-120b**.

## How it was reproduced

`tests/loader_portal_support.py` serves the Document Type replica over HTTP:
- Typing Transaction Type starts a "portal request". A full-screen DDS loading overlay appears and the rest of the form is disabled, as on the live portal.
- The server counts page loads. For the first N loads the request never finishes.

A real `BrowserSession` (Chromium, no MCP servers), the real no-progress watchdog, the real self-healer and the real Document Type executor run a mission-style attempt loop. Vision is off, as it is with a text-only model like gpt-oss-120b.

| | Result |
|---|---|
| **Before R18** | Each attempt spent about 250 s failing field after field against the blocked form. The failure was classed "unknown" and retried once; the second failure stopped for review. No refresh or restart happened. A direct `BrowserSession.restart()` during a mission raised `HIP_BROWSER_SWITCH_PROHIBITED_AFTER_MISSION_START`, so "close and reopen the browser" could never work. |
| **After, spinner clears once the page is refreshed** | Attempt 1 → page refreshed → attempt 2 fills the whole form: **pass** |
| **After, spinner survives the refresh** | Attempt 1 → refreshed → attempt 2 still stuck → browser closed and reopened (same profile) → attempt 3: **pass** |
| **After, the next runs** | The agent remembered that a refresh never cleared this spinner but a restart did. It restarted first and kept the refresh as a last resort. |

## Root causes and fixes

| # | Root cause | Fix |
|---|---|---|
| 1 | **The browser could not be restarted during a mission.** `start()` locks the browser after launch, to stop switching to a *different* browser. `restart()` relaunches through the same launcher, so it always hit the lock. | `restart()` relaunches exactly the browser and profile already selected; switching browsers is still forbidden. The Dell SSO cookies in the persistent profile survive (tested). If the old Chrome still holds the debugging port, it waits once and retries. An attached own Edge/Chrome (`use_own_browser`) is not ours to close, so a fresh tab replaces the stuck one. |
| 2 | **The watchdog could not tell "portal is loading" from "agent is stuck".** It stopped any attempt after 90 s, well before the 5-minute loading rule. The R16 field heartbeats also counted as progress while a spinner blocked everything, so a real spinner stall could be hidden. | The progress marker reports whether a blocking loader is on screen, using the existing classifier that ignores passive spinners. While one is: the watchdog allows the loading budget (`loading_watchdog_timeout_seconds` + `loader_grace_seconds`, 300 + 60 s by default); only a newly filled field counts as progress; and it stops with **`HIP_PORTAL_LOADING_STUCK`**. |
| 3 | **Spinner errors were swallowed field by field.** The broker's click/fill returned `False`, and the form executors caught every exception as "this field failed" and moved on. | Portal-level errors end the attempt at once: refreshed page, loader never cleared, stale overlay, expired login (`environment_faults.py`). Ordinary field failures are still retried as before. |
| 4 | **Fields disabled under the spinner failed instantly** as "read-only value mismatch", without waiting for the portal. | A control that is disabled or covered while a blocking loader is present waits for the portal first (bounded by the loading watchdog). |
| 5 | **Recovery was chosen from the error text only.** `HIP_PHASE_NO_PROGRESS_WATCHDOG` was "unknown" (one retry). A loader that caused a generic failure was never recognised. | New classes with bounded escalation ladders: **stuck loader** = refresh the page → close and reopen the browser; **stalled phase** = reopen the form → refresh → restart the browser. At failure time the page itself is checked: a loader still blocking on every sample over about 1.5 s makes it a stuck-loader failure, whatever the error text said. |
| 6 | **The refresh needed vision to confirm the spinner.** With a text-only model (gpt-oss-120b) no vision check is possible, and the fail-closed rule blocked the refresh. | The ladder's refresh and restart act on DOM evidence (blocking geometry for the whole loading budget). The in-action vision-confirmed refresh is unchanged when a vision model is available. |
| 6b | A recovery step that itself failed (a refresh that errors) ended the phase. | A failed step counts as used, and the next step (browser restart) runs at once. |
| 7 | Each ladder step needs a full loading budget, but the 20-minute phase clock did not grow. | Each recovery step extends the phase's time budget by what it needs. |
| 8 | **A human Resume** kept the used-up ladder and attempt count, so the next spinner stopped again at once. | Resume gives the phase a fresh ladder, time budget and attempt budget. |
| 9 | After the ladder ran out, the review only showed the last watchdog text. | The review says what was already tried: "HIP_PORTAL_LOADING_STUCK_AFTER_RECOVERY: … automatic recovery already refreshed the page, closed and reopened the browser. Check the portal/network, then press Resume." |
| 10 | A successful refresh appeared as a red "Error" on the step card. | It is recorded as a success; the reason stays in the refresh audit, and the mission trace shows "Automatic recovery: …". |

## gpt-oss-120b

gpt-oss writes an `analysis` channel before its `final` answer. A gateway without a reasoning parser returns both in one string ("analysis…assistantfinal{…}"), and the reasoning often contains JSON-like fragments. `extract_aia_response_text` and `extract_json_object` now keep only the final channel. That parser is shared by the form planner, section judges, vision runtime and AutoWebGLM bridge. Plain JSON replies parse as before.

Environment recovery (refresh/restart) is deterministic and never waits on the model. The model is still used for form-level recovery advice, judges and planning.

## Learning (RSI)

`<memory_dir>/runtime_recovery_ladder.json` (`data\hip_memory` by default, preserved by APPLY) counts, per phase, which step resolved the phase (`resolved`) and which did not (`not_resolved`).

A step that never resolved a phase (at least 2 misses, 0 successes), while a later step has resolved it, is moved to the end of that phase's ladder. It is never removed.

This file sits beside R17's `form_structure_memory` (form layout) and the existing flow-pattern, skill-blueprint and world-model stores.

## Settings (all optional; defaults shown)

```yaml
portal:
  loading_watchdog_timeout_seconds: 300   # the allowed loading time
runtime_self_heal:
  loader_grace_seconds: 60                # extra time for the in-page watchdog to act first
  max_browser_restarts_per_phase: 1       # "close and reopen the browser" steps per phase
  learn_recovery_ladder: true
```

With the defaults, a stuck spinner costs about 6 minutes before the refresh, and about 6 more before the browser restart. Lower `loading_watchdog_timeout_seconds` (for example to 120) for faster recovery.

## Tests

- `tests/test_v243r18_loader_recovery_ladder.py` (13 tests):
  - the watchdog with and without a loader;
  - both ladders, including exhaustion and the review message;
  - a refresh already done by the loading watchdog;
  - reclassification when a loader is still up;
  - learned reordering;
  - Resume;
  - the loading budget;
  - a failed refresh falling through to the restart;
  - the broker re-raising portal errors;
  - gpt-oss parsing.
- `tests/test_v243r18_real_browser_loader_ladder.py` (real Chromium):
  - restart during a mission keeps the profile's cookie;
  - the full refresh → restart → pass ladder against the stuck-spinner portal.

## Verification

| Check | Result |
|---|---|
| Full suite (190 files) | 1,376 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R18_INSTALL.ps1` R18 smoke checks | All pass (and the R17 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R18_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R18_INSTALL.ps1
```

Restart the Control Center and start a new mission. R18 includes R13–R17.

## Validation boundary

The stuck spinner was reproduced with a replica of the Dell loading overlay, and the browser restart with a locally launched Chromium. The live portal's reason for the stuck request is unknown: backend, network or session.

If the spinner survives the refresh and the restart, the review says so. The evidence is in:
- `runtime_self_heal/<phase>/a<NN>_*` (each failure and the step taken);
- `mcp_runtime/loading_watchdog/*` (loader samples and refreshes);
- `runtime_recovery_ladder.json` (what worked before).
