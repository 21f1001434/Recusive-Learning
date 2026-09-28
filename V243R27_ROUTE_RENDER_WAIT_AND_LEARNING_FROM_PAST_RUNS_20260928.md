# V243R27: model selection survives a slow listing; every mission learns from past runs and MLflow (2026-09-28)

## What was reported

1. On Live GO/NO-GO, **"Model selected by live task performance (one time)"** was **WARN**:

   ```
   HIP_ROUTE_NOT_COMMITTED: ReAct navigation controller could not reach …/securelink/doctypes;
   final observation={'target_match': True, 'target_usable': False, 'logged_in': True,
   'sso_transition': False, 'same_actual_surface': True, …}
   ```

2. "Is it learning from the old runs / MLflow and improving itself? If not, it needs to learn and improve."

## 1. The model-selection warning

**Why.**
- The browser was on the right page (`target_match`) and signed in (`logged_in`), but the Document Types listing had not finished rendering.
- The navigation controller spends one of its 4 steps on every "wait and re-observe". When no loading overlay is on screen, each wait lasts about half a second.
- So a listing that is committed and authenticated but still rendering failed after about 2 seconds. In the replica the old code failed in **2.3 s**, with exactly the observation above.

**Now.**

| Situation | What the agent does |
|---|---|
| Route committed, signed in, page still rendering | Waits for it: `portal.navigation_render_wait_seconds` (90 s), or longer when past runs showed this module needs more (at most `navigation_render_wait_max_seconds`, 300 s). The wait spends no navigation step (`await_route_render` in `mcp_runtime/navigation_react_trace.json`). |
| Still not rendered after that | Reloads the page **once** (a read-only GET of the listing), then waits again, for half as long. |
| Still not rendered | Fails as before with `HIP_ROUTE_NOT_COMMITTED`, now bounded, and the error says **why**: `usability` holds `reason` (`page_not_rendered`, `module_not_rendered`, `login_surface`, `document_loading`), the expected terms seen and the structural check. |

Each navigation is recorded in `mcp_runtime/navigation_render_waits.jsonl` (module, seconds, render wait, reloaded), and later missions learn from these records (section 2).

**The model selection itself:**
- The listing is opened once more if the first navigation does not finish.
- If the browser is on the exact listing route but the page is not yet judged usable, the qualification reads the page anyway. It gets up to 60 s for rows to appear. The questions are built only from the controls actually rendered; too few controls stop it without guessing.
- **A re-run that cannot finish keeps the model that passed the live task before.** The row passes with "kept: <model> (8/8 live questions correct, <date>); the re-run could not finish: <reason>". The lock is not touched.

## 2. Learning from past runs and MLflow

**What was already learned across runs:**
- the replay policy, which imports old run folders;
- the loader/stall recovery ladder (`runtime_recovery_ladder.json`);
- certified form skills, form-structure memory and the portal brain;
- the model champion.

**What was not:**

| Gap | Effect |
|---|---|
| **MLflow recorded nothing.** MLflow 3.16.1 (the pinned version) refuses the local `runs/mlruns` store HIP falls back to unless `MLFLOW_ALLOW_FILE_STORE=true` is set. The tracker is fail-open, so it switched itself off at mission start. In addition, `requirements.txt` did not list MLflow at all, and the patch wheels install without dependencies. | Without a configured MLflow server, no run reached MLflow. |
| **Nothing read MLflow back.** | MLflow was observability only. |
| **Old runs were learned only as whole-mission pass/fail.** | How long each phase and attempt really took, what stopped it (watchdog, phase budget, route), how long a module needed to render, which fields failed: none of this changed the next mission. |

**Now.**

**MLflow records again.**
- The tracker allows the local store (`MLFLOW_ALLOW_FILE_STORE=true`, only for a `file:` store or a plain path; a server URI is untouched).
- Each failed attempt is logged as `phase/<phase>/failure/<HIP_CODE>`, and each module's render time as `navigation/<module>/render_seconds`.
- `mlflow.set_tracking_uri` also exports `MLFLOW_TRACKING_URI` to the whole process. The tracker now restores it, so a later mission or learner in the same process no longer takes the previous run root's store as "configured".
- `APPLY_V243R27_IN_PLACE.ps1` installs `mlflow-skinny==3.16.1` if it is missing. protobuf stays on 5.29, which autogen needs.

**Every mission first learns from the past runs** (`hip_id_agent/run_history_learning.py`), before its first phase:

1. **Harvest, once per run.** A ledger (`data/hip_memory/run_history/run_facts.json`) records what was read. A run that was still running is read again once it has finished.
   - **Run folders:**
     - `mission_state.json`, `mlflow_async_events.jsonl` (per-attempt durations);
     - `<phase>/phase_execution_attempts.json` (failure codes);
     - the phase-budget and watchdog evidence;
     - `mcp_runtime/navigation_render_waits.jsonl`;
     - the self-heal trace (which recovery was followed by a completed phase);
     - the form runtime's failure summary (fields that failed) and its parent restorations.
   - **MLflow:** the configured server, or the local store. Runs whose folders are gone, or that ran on another machine logging to the same server, are learned from their metrics (per-attempt `duration_seconds` history, `failure/<code>`, render seconds) and tags.
2. **Lessons.** Each lesson is bounded and states why (`data/hip_memory/run_history/lessons.json`):

   | Lesson | From | Rule |
   |---|---|---|
   | `min_attempt_seconds[phase]` | durations of the attempts that completed the phase; stops by the phase budget or watchdog | p90 × 1.25; +50% of the configured value per run the budget stopped |
   | `phase_wall_seconds[phase]` | first attempt → phase complete | p90 × 1.25 |
   | `no_progress_watchdog_seconds[phase]` | the watchdog fired, and the phase then completed (the portal was slow, not stuck) | +50% per such run |
   | `navigation_render_wait_seconds[module]` | render times of each HIP module; routes that did not render in time | p95 × 1.5; +50% per failure (at most 4) |

   All lessons have the same limits:
   - they only **lengthen** a time budget, never beyond 3 × the configured value (`max_budget_multiplier`) or the render maximum;
   - they never shorten one, never skip a check, and never relax the mutation gate;
   - the live page stays authoritative.
3. **Applied to this mission:**
   - the self-healer's phase budget (`apply_learned_wall_budgets`);
   - each attempt's minimum budget and no-progress watchdog;
   - the browser's render wait per module.

   What was applied is written to `<run>/run_history_learning.json` and logged to MLflow (`hip.learned.*`, event `run_history_lessons_applied`).
4. **Reported, not applied:** the most frequent failure codes per phase, the fields that failed most often, and the recoveries that were followed by a completed phase.

**Where to see it:**
- **Control Center:**
  - tile **"Learned from past runs"**: `N runs`, `k lessons in use • m from MLflow`;
  - panel **"Learned from past runs · run folders + MLflow"** (Certified Tasks tab, below Recursive self-improvement): **Learn now**, the lessons with their reasons, and the per-phase history (completed / runs, p90 attempt, what stopped it, fields that failed).
- **CLI:** `python -m hip_id_agent.cli learn-from-runs` (`--show`, `--no-mlflow`, `--json`).
- **API:** `GET /api/learning/run-history`, `POST /api/learning/run-history/refresh`; `/api/runtime/status` → `run_history_learning`.

Settings (`run_history_learning`): `enabled`, `apply_at_mission_start`, `include_mlflow`, `max_runs` (300), `max_mlflow_runs` (100), `mlflow_timeout_seconds` (30), `time_margin` (1.25), `max_budget_multiplier` (3.0).

## Tests

`tests/test_v243r27_route_render_wait_and_run_history_learning.py` (17):

**Real browser.** The Document Types replica gains `boot_delay_ms` (header first, module later), `stuck_loads` (first loads never boot) and the live route path `/hybrid-integrations/securelink/doctypes`.
- A listing that renders after 5 s is waited for (`await_route_render`, no step spent) and recorded.
- A listing that renders only after a reload is reloaded exactly once.
- A listing that never renders fails bounded (< 60 s) with `module_not_rendered`.
- The model is selected on the slowly rendering listing (stand-in for Dell AIA, all questions correct).
- **The learning loop:** a past run records that the listing needed about 8 s. A mission configured with a 2 s render wait fails without learning. After learning from that run folder it waits about 12 s and succeeds without a reload.

**Model selection:**
- on the right route, the models are qualified from what is rendered;
- a re-run that cannot finish keeps the earlier model (mistral 8/8) and leaves the lock unchanged.

**MLflow:**
- the tracker records into the local store again (fails before the fix with MLflow's "filesystem tracking backend is in maintenance mode");
- runs that exist only in MLflow (no run folder) are learned from their metric history and failure metrics, once.
- one mission's tracker no longer redirects the next one's MLflow store.

**Learning:**
- run folders like `UHAUL-POASN-20260928-130654` (watchdog, then blocked) and a run completed after a 1500 s attempt give:
  - `min_attempt_seconds` 1875;
  - watchdog 135 s;
  - doctypes render wait 135 s;
- each run is read once;
- a running run is re-read when finished;
- a fast history never shortens a budget;
- the lessons change the next mission's self-healer budget and browser render wait, and only for the phases and modules concerned;
- the mission learns before its first phase and logs failure codes and render times to MLflow;
- the Control Center shows the lessons;
- defaults.

## Verification

| Check | Result |
|---|---|
| The reported failure, reproduced on the replica (listing at the live path; header first, module 12 s later) | Before: `HIP_ROUTE_NOT_COMMITTED` after **2.3 s** with `target_match: True, target_usable: False, logged_in: True, same_actual_surface: True`, as in the screenshot. After: accepted after 13.0 s (`navigate_target → await_route_render → accept_target`). |
| Listing that renders only after a reload / never renders | Reloaded once and accepted in 4.9 s / failed after 21 s with `reason: module_not_rendered` |
| MLflow with the pinned 3.16.1 and no tracking URI | Before: the tracker's start failed ("filesystem tracking backend is in maintenance mode") and nothing was recorded. After: runs are recorded and read back (per-attempt `duration_seconds` history, failure metrics, tags). |
| Control Center, real backend and browser | The "Learned from past runs" tile shows "2 runs • 3 lessons in use". The panel lists the lessons with their reasons. After a run that exists only in MLflow was added: "3 past run(s) learned (1 from MLflow); 5 lesson(s) in use. MLflow: read (1 new run(s))." |
| `learn-from-runs` CLI, `/api/learning/run-history`, `/refresh`, `/api/runtime/status` | Lessons and per-phase history shown; HTTP 200 |
| R27 tests | 17 passed |
| Closest suites (MLflow, R24 qualification, R25 certification job, R26, navigation controller, live certification, backend, Control Center, self-heal) with MLflow installed | 102 passed |
| Full suite (202 files) | 1,504 passed, 1 skipped (before the MLflow environment fix and its test; the R27 and MLflow files were re-run after it: 36 passed). Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| Dependencies | `mlflow-skinny==3.16.1` with `protobuf` 5.29.6: `pip check` reports no broken requirements (autogen-core 0.7.5 needs ~=5.29.3) |
| `VERIFY_V243R27_INSTALL.ps1` R27 smoke checks | `R27_ROUTE_RENDER_WAIT_AND_MODEL_KEPT_OK`, `R27_RUN_HISTORY_LEARNING_OK`, `R27_MLFLOW_LOCAL_STORE_OK` (and the R26 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R27_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R27_INSTALL.ps1
```

R27 includes R13–R26. No config.yaml change is needed; all new settings have defaults.

**The first mission after the update** reads every run already in `runs\` (once). If MLflow was installed and a server was configured, it also reads the MLflow runs. The Control Center tile then shows how many runs were learned and which lessons are in use; `learn-from-runs` shows the same without starting a mission.

## Validation boundary

- The live Dell listing's render time after SSO is not available here. The failure was reproduced with the same observation (committed route, signed in, module not rendered) on the replica.
- The lessons were computed from run folders and MLflow runs shaped like the live evidence (the R26 run's watchdog / stall codes, the attempt durations). On the live machine they come from your own `runs\` history and MLflow.
