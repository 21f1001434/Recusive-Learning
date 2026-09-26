# V243R21: Derived From and Usage in every row are filled on the live portal; gpt-oss-120b is used (2026-09-26)

## What was reported

On the live Document Type form, the agent did the following:
- it added all five "Attributes to Configure" rows and filled Attribute Name;
- it never filled **Derived From** or **Usage** in any row, including the first;
- the live overlay stayed on SELECTING ("selected target is being re-proven", 0 acting);
- the phase ran six cycles.

The runs also used **gpt-oss-20b**, although gpt-oss-120b is configured.

## Why the replicas passed and the live portal did not

On the live portal, every click and fill passes the **semantic action gate**:
- it proves the target before the action;
- it re-proves the target just before dispatch;
- it verifies the effect afterwards.

The overlay highlights each target as the gate proves it.

The replica tests switched the gate off, because they have no MCP evidence servers. That was the gap. With the gate on, the Document Type replica fails exactly as the live portal did.

## Root causes

The gate identifies a control by a value-free fingerprint: label, role, section and framework name. **Look-alikes share one fingerprint**:
- a row's "Derived From" has the same fingerprint in every attribute row;
- every option of the Usage multi-select carries the fingerprint "Usage";
- a `dds-button` host shares its inner button's fingerprint.

| # | Where | What happened | Fix |
|---|---|---|---|
| 1 | Re-proof just before dispatch (`revalidate`) | It found several controls with the target's fingerprint. Look-alikes can be told apart only while the DOM generation is unchanged, but the generation always advances before dispatch: the overlay highlighting the target, hover and focus classes. It refused with `HIP_SEMANTIC_TARGET_DRIFT: ambiguous_after_rerender`, so no dropdown in a repeated row could even be opened. With several attribute rows on screen, that is every Derived From and every Usage. The same refusal hit the "+" (`match_count=2`) and the Usage options (`match_count=9`). | With look-alikes present, the target is re-proven through the **executor's own row-exact locator**. It must still be the same control (same fingerprint) in the same row. A control that was re-created, or that moved to another row, is still refused. |
| 2 | Effect check after the action (`verify_and_learn`) | It compared the **first** look-alike, not the control acted on. A Usage option click that worked was reported as `HIP_SEMANTIC_EFFECT_NOT_PROVEN`, because it compared "Select all" and saw no change. The field was retried, so each attempt landed one more option: three of four, then a repair cycle for the fourth. | The effect is read from the element the action used. `aria-selected`, `aria-checked` and a checked inner checkbox now count as the control's selection state. |
| 3 | Failure report | The field said only "control did not reach a stable exact expected value", which hid the refusal. | A failed field names the last refused portal action and its reason, for example `last refused portal action: HIP Portal DDS combobox: HIP_SEMANTIC_TARGET_DRIFT: …`. |

## Why gpt-oss-20b took over

| # | Cause | Fix |
|---|---|---|
| 4 | A tournament's winner was chosen by each model's own `confidence` field, which is not comparable across models: gpt-oss-20b answers 0.98 where gpt-oss-120b answers 0.72. | Each on-prem model has a capability tier: gpt-oss-120b 1.00, llama-3-3-70b 0.86, mistral-small 0.78, gemma-3-27b 0.76, gpt-oss-20b 0.72, llama-3-2-3b 0.40. It weighs 30% (`capability_weight`). A weaker model still wins when the stronger one gives no usable answer. |
| 5 | Learning tournaments explored the **least-tried** models first, so gpt-oss-120b, the model with the most history, could be left out of every per-field decision. | The strongest available model, or a champion proven by downstream evidence, always takes part. Challengers fill the other slots. |
| 6 | The winner of those tournaments collected the downstream reward and became role champion. It was then exported as `HIP_MODEL_ROUTER_SELECTED_TEXT`, which overrides `aia.model` for **every** default model call. | Champions are ranked capability-aware. A champion weaker than the configured model (`aia.model`, gpt-oss-120b) never replaces it for default calls, unless the configured model is proven down. A stale gpt-oss-20b champion in `data\hip_memory\model_portfolio` is re-ranked on the next run. |

Settings (`model_portfolio`): `prefer_strongest_model: true`, `capability_weight: 0.30`, `primary_text_model: ""` (empty means `aia.model`). They default correctly without any config.yaml change. `prefer_strongest_model: false` restores pure evidence ranking.

## Faster

| # | Cause | Fix |
|---|---|---|
| 7 | On every event (about four per action), the live-view recorder re-read `agent_live_view.json`, masked **every** history entry again and rewrote the file. At 120 entries of about 74 KB each, that is a 9 MB file and about 1.1 s per event. | The state is kept in memory. Only the new entry is masked. History entries keep what the decision timeline shows; `current` keeps the full evidence. |

With the live gate on, on the replicas:

| Run | Before | After |
|---|---|---|
| Rule | 170 s | 91 s |
| Document Type | failed, or 1,100 s over 2 cycles | 288 s, 1 cycle |

## Verified

Every phase replica was run with the **live semantic gate on**, using config.yaml's adaptive evidence policy and the live overlay. Each list was created with its own "+". Every case passed in **one cycle**, with 0 `HIP_SEMANTIC_TARGET_DRIFT` and 0 `HIP_SEMANTIC_EFFECT_NOT_PROVEN`.

| Phase / tab | Gated actions | Time |
|---|---|---|
| Data Map | 7 | 14 s |
| Source Document Type (identifier + 5 attribute rows; Derived From, Usage ×4, Expression) | 106 | 288 s |
| Rule (2 conditions) | 36 | 101 s |
| Source Transport Profile | 35 | 92 s |
| Target Transport Profile | 35 | 92 s |
| BizFlow Flow Details | 2 | 7 s |
| BizFlow Configure Source | 36 | 102 s |
| BizFlow Configure Target(s) | 54 | 178 s |
| BizFlow Configure Routing | 42 | 140 s |

## Tests

- `tests/test_v243r21_gate_lookalikes.py` (5):
  - a repeated-row dropdown is re-proven through its own locator after the DOM generation advances (the fingerprint alone still reports `ambiguous_after_rerender`);
  - a re-created control is still refused;
  - a multi-select option click is verified on the option clicked;
  - Document Type in one cycle with the gate on (5 rows, Derived From, all four Usage values);
  - Rule with the gate on.
- `tests/test_v243r21_model_preference.py` (6):
  - gpt-oss-120b beats a more self-confident gpt-oss-20b;
  - gpt-oss-20b wins when gpt-oss-120b gives no usable answer;
  - learning never leaves out the strongest model;
  - a weaker champion never replaces `aia.model`;
  - the configured model yields only when it is proven down;
  - capability tiers.
- `tests/phase_replica_support.py`: `run_phase_replica(gate=True)` runs a replica with the live gate and overlay, and reports `gate_stats`.
- The R241/R242 portfolio tests that assert gpt-oss-20b winning on its self-reported confidence now declare `prefer_strongest_model=False`. They test the reward and champion bookkeeping, which is unchanged.

## Verification

| Check | Result |
|---|---|
| Full suite (196 files) | 1,420 passed, 1 skipped. The only failure is the checkout-only `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden`, which needs the gitignored `uploads/*.jar` (present in the package). `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| R21 tests | 11 passed: 5 gate tests (including Document Type and Rule with the live gate on) and 6 model preference tests |
| Every phase replica with the live gate on | 9/9 pass in one cycle, 0 target-drift and 0 effect refusals (table above) |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R21_INSTALL.ps1` R21 smoke checks | `R21_LOOKALIKE_CONTROLS_REPROVEN_OK`, `R21_STRONGEST_MODEL_PREFERRED_OK`, `R21_LIVE_VIEW_COMPACT_OK` (and the R20 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R21_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R21_INSTALL.ps1
```

R21 includes R13–R20. config.yaml needs no change, and `data\hip_memory` is preserved.

## Validation boundary

The live DOM was not available. The failure was reproduced by running the replicas with the gate the live portal uses. The refusals and their counts match the live overlay: Derived From selected but never acting, and Usage never filled.

The MCP evidence servers (Playwright MCP, DevTools MCP, HIP Intelligence MCP) are not running in the replicas. On the live portal they add evidence to the same gate. They do not change which element the executor's locator points at.
