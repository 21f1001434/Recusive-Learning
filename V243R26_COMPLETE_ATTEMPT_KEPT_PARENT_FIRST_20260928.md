# V243R26: a filled Document Type is kept, retries get time, and the section above is filled first (2026-09-28)

## What was reported

Run `UHAUL-POASN-20260928-130654`, Source Document Type:
- **attempt 3** filled the whole form, but the phase did not complete;
- **attempt 4** showed a reopened form with Name, Transaction Type, Description and Operation filled, but **Data Format Type and every Derived From, Usage and Validation Type below it empty**;
- then: *HIP_PHASE_STALL_AFTER_RECOVERY … HIP_PHASE_NO_PROGRESS_WATCHDOG: no new verified field before the phase wall budget (0 progress extension(s) used)*. Activity: "wait: portal_ready".

"It needs to fill the section above correctly, and only then do the dropdowns below get their values."

## Why

| # | Cause |
|---|---|
| 1 | **An attempt that had filled everything was stopped.** At its deadline, the phase budget stops an attempt that has not verified any *new* field since the last deadline. An attempt whose form is complete adds no new fields while it finishes its read-back and evidence, so it was stopped. Recovery then **reopened a blank form**. The no-progress watchdog re-proves the live form before stopping; the budget did not. |
| 2 | **The retry was starved.** An attempt's budget was only what earlier attempts had left of the shared phase budget. Attempt 4 got almost nothing and was stopped "with 0 extensions" while the portal was still loading. |
| 3 | **Progress counted across attempts.** The fields attempt 3 had verified stayed in the page's verified set. When attempt 4 verified them again on a reopened form, that was not new progress. |
| 4 | **A cleared parent was not restored first.** The lower dropdowns (Operation, Derived From, Usage, Validation Type) list values only while Data Format Type (and each row's own Derived From) holds its value. When the portal cleared an already verified parent (for example a late Transaction Type response), every child dropdown opened empty and retried until the attempt's time ran out. In the replica: 21 empty dropdown openings, 170 s. |

## What changed

1. **The form is kept when it is complete.**
   - The progress marker now reports `fill_complete` once every field of the phase is filled and verified.
   - At the deadline, such an attempt gets `runtime_self_heal.finalize_grace_seconds` (600 s) to finish, at most `max_finalize_extensions` (2) times, instead of being stopped.
   - Before any stop, the phase budget now re-proves the live form read-only, as the watchdog does.
   - When the form is exact, the stop is reported as `HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL`, and the mission continues to the judges **without reopening the form**.
2. **Every retry gets a fair budget.** An attempt gets at least `runtime_self_heal.min_attempt_seconds` (900 s = 15 min), even when earlier attempts used most of the phase budget.
3. **Progress is counted per attempt.** `begin_phase_attempt_progress` gives each attempt its own verified-field set, fill/click baseline and `fill_complete` flag. Re-verifying a reopened form counts.
4. **The section above first.** Before any field is filled, each dropdown it depends on is read back from the live form, root first. For the Document Type these are Data Format Type, then Operation, then Derived From. One that the portal cleared or changed is selected again before the field is filled. This applies to every phase form, and each restoration is recorded (`parent_restorations`).
   - In the replica, the same reset now costs **0** empty dropdown openings and 58 s.

## Replica

`tests/fixtures/document_type_full_dds.html` gains the live behaviour, behind flags:
- `__liveOptionsAfterFormat`: every section is on the page from the start; Operation, Derived From, Usage and Validation Type list nothing until Data Format Type is chosen.
- `__formatAfterTransaction`: Data Format Type lists nothing until the Transaction Type lookup returns (`__transactionRequestMs`).
- `__formatResetOnceAfterMs`: a late portal response clears Data Format Type once after it was chosen.

With all three, and the loader after Transaction Type, the full 5-row Document Type completes in one cycle. Every Derived From, Usage and Validation Type is correct.

## Tests

`tests/test_v243r26_complete_attempt_kept_parent_first.py` (8):
- a completely filled form gets time to finish (`extended_to_finish_verification`);
- a stopped attempt whose live form is exact is reported as `POST_COMPLETION_STALL`, not reopened;
- an attempt neither progressing nor exact is still stopped;
- the finishing time is bounded;
- each attempt counts its own progress;
- the mission gives every retry the minimum budget and passes the read-only proof;
- real browser: the lower dropdowns are filled only after Data Format Type, and `fill_complete` is reported;
- real browser: a Data Format Type the portal cleared is selected again before its children (0 empty dropdown openings).

## Apply

```powershell
.\APPLY_V243R26_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R26_INSTALL.ps1
```

R26 includes R13–R25. If you keep your own config.yaml, the new `runtime_self_heal` keys (`min_attempt_seconds`, `finalize_grace_seconds`, `max_finalize_extensions`) apply with their defaults. Restart `bun run platform`, then run the mission again.

## Validation boundary

- The live run's own evidence was not available. The causes above were reproduced from the screenshots, the recorded failure message and the code.
- The live Document Type form behaviours are modelled in the replica: sections present, lower dropdowns empty until Data Format Type, and the Transaction Type lookup. The replica does not reproduce the live portal's exact timing.
