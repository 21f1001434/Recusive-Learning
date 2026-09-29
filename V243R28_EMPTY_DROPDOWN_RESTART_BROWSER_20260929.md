# V243R28: a dropdown with no values → close and reopen the browser, same page, open the form, fill it (2026-09-29)

## What was reported

- The first run filled everything correctly.
- In the second run a dropdown showed **no value, or only some text**. The screenshot shows the live view on the Create Document Type form with **Data Format Type** selected and the fields below it empty.
- This can happen **at any stage** (Document Type, Transport Profile, …). The recovery that works is:
  1. close the browser;
  2. reopen it;
  3. go to the exact same link;
  4. open the form as usual;
  5. fill it.

## What was happening

Reproduced on the Document Type replica. Its lists now break from the second form load of a browser session until the browser is closed, as on the live portal:

| | Before R28 |
|---|---|
| What the agent saw | Data Format Type opened with "No data found". The driver then typed search text into the empty list (the "some text" in the field). |
| How the attempt ended | After about **2.8–3.9 minutes**: *"control did not reach a stable exact expected value after blur/rerender"*, and every field below failed as *"dependency failed"*. Nothing said the list was empty. |
| Classification | `exact_value_mismatch`, as if a value were wrong |
| Recovery | Re-opened the form **in the same browser** (twice), then refreshed it. The browser was never closed, so the lists stayed empty: 4 attempts over more than 15 minutes, still failing. |

## What changed

1. **An empty list is recognised.**
   - When a dropdown opens with no options, or only with a placeholder text ("No data found", "No results", "No options", "Nothing found" …, even when shown as an option), the driver closes and re-opens it for up to 12 s, at least 4 times however slow each opening is.
   - A list that is still loading ("Loading…"), or one that only needed a slow lookup, is waited for and then used normally.
   - A list that stays empty is not typed into.
   - This applies to single and multi-select dropdowns, in the Document Type executor and in the executor all other phases use.
2. **The fields above are checked first.** A dropdown below a field that is not filled yet is expected to be empty. A field above that the portal cleared is selected again (R26 parent-first), and the fill continues.
3. **When every field above holds its value, the attempt ends at once** with a clear reason:

   > HIP_DROPDOWN_OPTIONS_EMPTY: phase=source_document_type; the 'Data Format Type' dropdown opened with no values (the portal showed 'No data found') although every field above it holds its value; the portal's lists stopped loading in this browser session. Recovery: close and reopen the browser, return to the same page, open the form and fill it again.

4. **The recovery is your recovery.** A new failure class, `dropdown_options_empty`, has one step: **close and reopen the browser**.
   - The same browser and profile are used, so the Dell SSO sign-in is kept.
   - The agent then goes to **the same phase link** (`PHASE_URLS[phase]`).
   - The next attempt **opens the form as usual and fills it** from input.json.
   - This happens at most `runtime_self_heal.empty_options_browser_restarts` (2) times per phase. Each phase has its own count, because it can happen at any stage.
   - If the lists are still empty after that, the phase is held for you with *HIP_DROPDOWN_OPTIONS_EMPTY_AFTER_RECOVERY: … closed and reopened the browser …* (Resume continues).
   - The live view shows "Automatic recovery: restart browser session after: dropdown options empty".
   - Whether the restart resolved it is remembered (`runtime_recovery_ladder.json`, family `lists`), and R27's run-history learning reads it.

**After R28, on the same replica:**
- the empty Data Format Type is recognised, and the browser closed and reopened, 32 s into the second run;
- the agent returns to the same page and opens the form;
- the next attempt fills everything in 62 s: a new browser session, every dropdown filled, no empty list.

## Tests

`tests/test_v243r28_empty_dropdown_restart_browser.py` (9). The replicas gain the live symptom: `window.__lookupsBrokenFromLoad = N` (lists break from the N-th form load of a browser session; a reload or a re-opened form keeps it, a browser restart clears it) and `window.__hipLookupsBrokenNow`. They are in the Document Type replica and in the shared DDS kit used by Data Map, Rule, Transport Profile and BizFlow.

**Real browser:**
- **The mission-style loop.** Run 1 fills. In run 2 (same browser):
  - attempt 1 ends with `HIP_DROPDOWN_OPTIONS_EMPTY` for 'Data Format Type' ("No data found") in under 90 s;
  - the class is `dropdown_options_empty` and the action `restart_browser_session`, and the browser really restarted;
  - attempt 2 passes in the new browser session with every dropdown filled;
  - the ladder memory records the restart as resolved.
- **A slow list** (Data Format Type shows "No data found" on its first 3 openings, then its values) is re-opened until the values come. The form completes with no restart.
- **Any stage: Transport Profile** with empty lists ends the attempt with `HIP_DROPDOWN_OPTIONS_EMPTY: phase=source_transport_profile`, in under 60 s, with no search text left in any dropdown.

**Units:**
- empty / loading / filled list detection;
- the confirmation window: a list that fills later is used, one still "Loading…" gets a second window, one that stays empty is reported;
- a dropdown below an unfilled field is not a broken portal;
- classification (also when the message mentions "value mismatch" or "session"), the environment-fatal code and the default;
- at most two browser restarts per phase, each followed by the same page, then held with the recovery summary;
- another phase gets its own restarts;
- the mission's hold message.

## Found and fixed during validation

- The first confirmation loop treated a list still showing "Loading…" as usable and stopped waiting. It now waits for real options.
- Each re-open waited 3.2 s for options, so the 12 s window allowed only two re-opens. A list that filled on its fourth opening was called empty; in the full suite this showed up as an intermittent failure of the slow-list test.
- Re-opens during the confirmation now poll for 1 s, and at least 4 openings are made.

## Verification

| Check | Result |
|---|---|
| The reported symptom on the replica (run 1 in a browser, run 2 in the same browser with empty lists) | Before: 4 attempts over more than 15 min (about 2.8–3.9 min each), classified `exact_value_mismatch`; recovery re-opened the form / refreshed in the same browser and never recovered. After (final code): run 1 filled in 108 s; in run 2 the empty Data Format Type ended attempt 1 and the browser was closed and reopened ("relaunched_same_browser_and_profile") 32 s after run 1; attempt 2 on the same page filled everything in 62 s; new browser session, no empty list. |
| A slow list (fills on its 4th opening) | Waited for: 4 openings in 10.9 s, XML committed, no restart |
| R28 tests | 9 passed |
| Closest suites (R26 parent-first, R18 real-browser loader ladder, R14 full Document Type, R15 Transport Profile / Data Map / Rule / BizFlow, R17 variants, self-heal loop, DDS commit layer) | passed |
| Full suite (203 files) | 1,514 passed, 1 skipped. Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R28_INSTALL.ps1` R28 smoke checks | `R28_EMPTY_DROPDOWN_DETECTED_OK`, `R28_RESTART_BROWSER_RECOVERY_OK` (and the R27 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R28_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R28_INSTALL.ps1
```

R28 includes R13–R27. No config.yaml change is needed; `runtime_self_heal.empty_options_browser_restarts` defaults to 2. Restart `bun run platform` and run the mission again.

## Validation boundary

- The live cause of the empty lists (the portal's lookups failing inside one browser session) is not visible here. It is modelled from your description: the first run works, later runs in the same browser show empty lists, and closing and reopening the browser fixes it.
- If you run the mission in your own already-open Edge/Chrome (attached, not launched by the agent), "close the browser" becomes:
  - a brand-new tab, with the stuck one closed;
  - then the same link and the same form.
