# Rules Deep Capability Learning Milestone — 2026-08-25

## Objective

Extend the HIP Browser Intelligence Platform from Data Maps + Document Types into a deep Rules learner that understands both the Rules listing and the exact production-safe Rule form transaction. The learned knowledge must be reusable by future tasks without persisting customer-specific Rule names, condition values, mapping identifiers, credentials, cookies, or authorization headers.

## Implemented capability path

```text
Rules listing
  -> search/filter/pagination
  -> exact entity row
  -> expand row
  -> Edit / Clone / View / Details / History / Audit discovery
  -> Migrate / Deploy / Delete safe prerequisite probes
  -> unsaved Create Rule
       -> Rule Details
       -> Actions
            -> Action Type
            -> async Mapping Identifier
       -> Conditions
            -> Execute Action(s) When
            -> Condition row 0 exact
            -> Conditions + exact +1 transition
            -> Condition row 1 exact
            -> ... sequentially
  -> exact form/row verification
  -> UI/API causal evidence
  -> value-free deterministic replay promotion
```

## Production Rules logic is reused

The new deep learner intentionally calls the existing hardened Rules helpers instead of implementing a second simplified filler. This carries forward the live fixes for:

- global form validity before the Conditions `+` action;
- prerequisite Rule/Action commitment;
- Action Type → Mapping Identifier parent-child mount;
- asynchronous Mapping Identifier commit and timeout reconciliation;
- physical Condition row rebinding after Angular rerenders;
- exactly one row added per `+` transaction;
- complete current row before the next row is created;
- temporary structural Rule-name recovery when an existing name would otherwise block row creation;
- restoration of the exact requested Rule name for unsaved verification.

## Capability Graph v5

The persistent graph stores structural Rule knowledge only. Replay steps retain:

- input path;
- section;
- row kind/index;
- action type;
- semantic control identity;
- dependency node IDs;
- exact verification policy;
- wait/mount contracts;
- successful capability IDs;
- API contract IDs.

Current Rule names, condition values and mapping-identifier values are always reloaded from the current task/input and are not stored as reusable replay values.

## Safe mutation discovery

Migrate, Deploy and Delete can be learned without executing their backend mutation. During a probe the browser installs a route barrier that aborts POST/PUT/PATCH/DELETE and mutation-token GET requests. The agent may then learn the dialog, prerequisite controls, confirmation actions, endpoint and request shape while preventing backend delivery.

## Frontend/backend integration

Added:

- CLI: `learn-rules-deep`
- FastAPI: `POST /api/rules/deep/start`
- Streamlit: `Deep Learn Rules`
- PowerShell: `RUN_LEARN_RULES_DEEP.ps1`
- Run indicator: `Rules Deep`

## Verification

The complete project regression inventory passes 568/568 tests in three bounded groups: 217 + 145 + 206. Python compilation and CLI wiring also pass. Authenticated Dell HIP live execution is not available in this environment and therefore is not claimed.
