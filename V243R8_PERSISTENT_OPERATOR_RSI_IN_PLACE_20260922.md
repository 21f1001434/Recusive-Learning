# V243R8 Persistent HIP Operator / Open-Ended RSI / In-Place Upgrade

## Goal
V243R8 changes the execution model from **run-oriented automation** to **goal-oriented convergence**.

A user may ask for an arbitrary HIP task such as:

- `edit Document Type "ABC" and change Version to 2.0`
- `find Rule "MAP1" and change Operator to Equals`
- `find BizFlow "UHAL" and change destination_value to UHI1002`
- `search Data Map "X" and open Edit`
- `configure Source Transport Profile from input.json`

One goal owns one authenticated browser. A failed cycle is learning evidence, not a terminal result.

## New control loop

```
User goal
  -> classify family/operation/entity
  -> load Portal Brain / capabilities / recipes / skills
  -> deterministic replay when proven
  -> otherwise adaptive live discovery
  -> exact verification
  -> failure trace -> model portfolio -> self repair
  -> replay/model/skill/RSI update
  -> retry SAME GOAL in SAME browser
  -> repeated stagnation -> human recovery (browser remains open)
  -> automated PASS
  -> final human review
  -> human PASS => COMPLETE
  -> human correction => back into learning loop
```

`persistent_operator.max_goal_cycles: 0` means there is no attempt-count termination. The process can still be stopped explicitly by creating `STOP_HIP_OPERATOR` in the run directory or stopping the controller.

## RSI
`recursive_self_improvement.max_recursive_cycles: 0` means RSI is open-ended across goal cycles. Each cycle remains bounded/auditable internally, but a failed goal does not terminate RSI. Every cycle feeds replay policy, model portfolio and skill confidence.

Source-code self modification remains disabled. RSI changes learned policy/model/skill/recipe behavior only.

## Inline edits
Explicit values in natural language are converted to a **run-local patch JSON**, never long-term portal memory.

Example:

```
hip-agent operate-hip "edit Document Type 'ABC' and change Version to 2.0" \
  --allow-portal-mutation \
  --confirmation "I APPROVE THIS HIP PORTAL MUTATION"
```

The value `2.0` is task input. Portal memory stores semantic field/control knowledge, not customer values.

## Final human gate
Automated success is not final when `require_final_human_confirmation=true`.
The Control Center receives a final review request. `Looks correct` completes the goal. `Needs correction` re-enters the same learning/self-heal loop.

## In-place upgrade
Use `APPLY_V243R8_IN_PLACE.ps1` or extract the `IN_PLACE_OVERWRITE` ZIP directly over the existing HIP project root.

The update intentionally does **not** overwrite:

- `.env`
- `config.yaml`
- `input.json`
- `runs/`
- `data/hip_memory/`
- user upload/runtime evidence

New config keys have code defaults, so an older config.yaml remains valid.

## Final verification note — in-place installed CLI
The verified in-place updater now force-reinstalls the exact bundled R8 wheel into the active Python environment after copying source files. This prevents an existing `hip-agent` console command from continuing to import an older installed 2.4.3 build. Use `-SkipWheelInstall` only if you intentionally manage package installation separately. The updater also verifies that `python -m hip_id_agent --help` contains `operate-hip` before reporting success.
