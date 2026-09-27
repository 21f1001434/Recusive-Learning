# V243R22: the agent keeps healing while it makes progress; the champion is earned on results; learning runs every phase (2026-09-27)

## What was reported

On the live Document Type form:
- rows 1–3 were filled, row 3's Usage was half done, and rows 4–5 were empty;
- the phase then asked for human review after **attempt 1**: "Automated: BLOCKED, deterministic: not proven";
- the agent did not keep healing itself.

The dashboard showed:
- Model Champion **gpt-oss-20b**;
- Recursive Improvement **Cycle 0** (best 0.000);
- Induced Skills **0**;
- Replay Policy **0/1**.

## Why it stopped and asked

| # | Cause | Fix |
|---|---|---|
| 1 | Every field action waited for a **4-model AutoWebGLM vote**, up to 12 s each, even though the executor had already bound the exact control and value (a "vetted intent"). The replay policy never reached exploitation, so every action ran as "learning" with the full portfolio. A five-row Document Type has about 100 such actions. | A vetted intent asks **one** model: the strongest available, or a champion proven by results. Every 10th vetted intent also asks one least-tried challenger, so the ranking keeps learning (`autowebglm.vetted_intent_parallel_models: 1`, `vetted_intent_challenger_every: 10`). Open decisions (planning, judges, self-repair, recovery advice) still use the portfolio. |
| 2 | The phase attempt ran under a hard **20-minute `asyncio.wait_for`**. It cancelled the attempt in the middle of row 3's Usage and left the popup open, although every field so far had been verified. The loop's wall-clock stall guard then handed the phase to a human. | **Progress earns time.** When the budget is reached and the attempt has verified new fields since the last deadline, it gets another 10 minutes, up to 6 times (`runtime_self_heal.progress_extension_seconds: 600`, `max_progress_extensions: 6`). The same check runs before the stall guard asks a human. Progress counts only distinct verified fields, committed fills and distinct clicked targets, so a click loop cannot earn time. |
| 3 | A stop without progress looked like a bare timeout. | It is now classified as `phase_no_progress`. The agent runs its own recovery ladder first (reopen the form from input.json, refresh, restart the browser), and asks a human only after that. |

The self-healer's own wall check now also counts the time earned by progress and recovery steps.

## The dashboard values were real — and why they were stuck

| Tile | What it reads | Why it was stuck | Now |
|---|---|---|---|
| **Model Champion: gpt-oss-20b** | The stored planning / action-selection champion. | A tournament's winner was picked partly on each model's own `confidence` field. A model that proposed the **same action** as the winner got only 35% "shadow" credit, because the proposal comparison included that confidence. gpt-oss-20b answers more confidently, so it collected full credit for the deterministic executor's successes, 179 dream cycles in a row. gpt-oss-120b could never catch up. | **Fair decision credit**: a model is scored on what it *decided*, not on its confidence. The same decision as the executed one shares its real outcome. A different decision scores 0 when the executed one was proven right, and is not scored at all when it failed. Evidence stored under the old rule is discarded once, and champions are re-earned. The tile shows the model that actually answers default calls (gpt-oss-120b); the detail line shows the champion and the number of scored decisions. |
| **Recursive Improvement: Cycle 0, best 0.000** | The RSI engine's cycle counter. | The RSI cycle (replay-policy dreaming, model-champion dreaming, skill review) ran only after a **whole mission** finished. A mission held at Document Type never finished. | It runs after **every phase attempt**. A failed attempt is rewarded with its verified share (for example 0.6 when 60% of the fields were verified). |
| **Induced Skills: 0** | Task skills induced after a judge and a human pass. | It did not show the R19 certified form skills at all. | It adds certified form skills. The detail line shows certified forms, forms still learning, task skills and stale skills. |
| **Replay Policy: 0/1** | Mission-level exploitation policies. | A policy becomes exploitation only after successful whole-mission episodes. None has completed yet. | Unchanged: it moves when missions complete. It no longer slows every click, because vetted intents no longer depend on it (fix 1). |
| **MLflow Async: ready** | MLflow client availability. | Accurate. | Unchanged |

## Tests

`tests/test_v243r22_autonomy_rsi.py` (9):
- a slow attempt that keeps verifying fields is not cancelled mid-form;
- an attempt without new verified fields is still stopped;
- extensions are bounded;
- the healer's earned time counts in its wall budget and resets on a human Resume;
- a vetted action asks one model, and every 10th asks a challenger;
- models with the same decision share its real outcome;
- champion evidence from the old rule is re-earned, and gpt-oss-120b answers meanwhile;
- every phase attempt runs a recursive-improvement cycle;
- the mission loop credits progress before it ever asks a human, and learns after each attempt.

## Also fixed: a test-harness navigation race

Under heavy parallel load, the R19 real-browser operation tests failed intermittently with `Page.goto: net::ERR_ABORTED`: the previous Save's redirect was still in flight when the next operation opened the listing.

The live `BrowserSession.goto_base_and_complete_sso` already tolerates this. It inspects the browser state and retries on the lighter "commit" lifecycle. The test harness (`tests/loader_portal_support.patch_navigation`) replaced it with a single bare `goto`; it now retries once the same way.

## Verification

| Check | Result |
|---|---|
| Full suite (197 files) | 1,428 passed, 1 skipped. The one-time failure of `test_v243r19_operations_real_browser.py` under load was traced to the harness race above. That file and the R18 real-browser loader tests then passed 6/6 twice, each time with three other browser tests running in parallel. The checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| R22 tests | 9 passed |
| R21 / R20 / R19 portfolio and replica tests | Pass: 31 portfolio tests, the gate-on phase tests and the live-"+" tests |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R22_INSTALL.ps1` R22 smoke checks | `R22_PROGRESS_EARNS_TIME_OK`, `R22_VETTED_INTENT_ONE_MODEL_OK`, `R22_FAIR_CHAMPION_AND_PHASE_RSI_OK` (and the R21 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R22_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R22_INSTALL.ps1
```

R22 includes R13–R21. config.yaml needs no change: the new settings default as described. `data\hip_memory` is preserved. The model portfolio's stored champion evidence is re-earned once under the fair rule; the previous champions are kept in the file as `previous_role_champions`.

## Validation boundary

The mission-level changes were checked by unit tests of each piece and by a structural check on the mission loop. The mission runner (`run-full-dummy-fill`) has no browser-level test harness in this repository, and the local mission UAT uses its own runner.

The first live run after applying R22 is the end-to-end proof. It should show three things:
- `phase_progress_budget_attempt_NN.json` files with `extended_for_verified_progress`, if Document Type still needs more than 20 minutes;
- `recursive_self_improvement_attempt_NN.json` in each phase folder;
- the Recursive Improvement tile moving during the run.
