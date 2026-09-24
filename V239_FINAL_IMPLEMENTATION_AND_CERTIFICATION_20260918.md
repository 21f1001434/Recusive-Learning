# HIP Portal Agent V239 / v2.3.9 — Final Implementation & Certification

## Scope

V239 extends the certified HIP automation runtime with a governed Universal Portal Operator and Skill Induction Engine while retaining V238 asynchronous MLflow observability and the V237/V236/V235/V234 execution protections.

## Universal portal-learning behavior

- Learns unfamiliar page families, visible actions and form controls from the current live portal.
- Accepts user-requested tasks beyond the original seven HIP phases.
- Uses the current `input.json` as the sole business-value authority for arbitrary form filling.
- Requires exact live readback for every applicable nonblank runtime-input leaf before a form fill is complete.
- Executes unknown user-requested visible actions only through bounded semantic live-goal discovery.
- Mutations remain behind the existing explicit mutation authorization gate.

## Skill Induction Engine

A successful exact-verified activity can be promoted into a reusable skill. The skill stores:

- value-free ordered workflow structure;
- semantic action labels and risk class;
- page-family context;
- form input paths and semantic field blueprints;
- section / repeatable-row identity;
- success/failure counts, confidence, freshness and drift state.

The skill does **not** persist:

- customer/business values;
- passwords or secrets;
- CSS selectors or XPath;
- transient framework/browser ids;
- screen/viewport coordinates or bounding boxes;
- the environment-specific URL learned on the successful run.

When a future request matches a validated skill, the stored workflow is instantiated using the current start URL, current entity and current `input.json`. Every step must be re-proven on the current live page. A skill cannot broaden mutation permissions.

If a non-mutating replay step drifts, V239 records negative skill evidence and restarts through adaptive live discovery. Mutation-step failures are never blindly retried. Repeated contradictions demote the skill to `drift_suspect`. Effective skill confidence decays with age, and successful rediscovery can reinforce the skill again.

## Async MLflow

`mlflow-skinny==3.16.1` remains an asynchronous, fail-open observability layer. Mission, phase, step, judge, recovery, input-coverage, skill activation/induction and final-result telemetry can be emitted without becoming an action-authority dependency. When no remote tracking URI is configured, V239 uses a local filesystem MLflow store under the HIP run root.

## Control Center additions

- Universal portal task planner/runner.
- Current skill activation details in task-plan output.
- Induced Skill Library table.
- Validated / stale / drift-suspect skill counts.
- Effective-confidence and last-success visibility.
- MLflow Async runtime status.

## Exact-package verification

The clean V239 archive was extracted to a new directory and verified from that extracted copy.

- Test inventory: **1,197 collected**.
- Result: **1,196 passed, 1 skipped, 0 failed**.
- Focused V235–V239 regression: **32 passed, 0 failed**.
- Source manifest: **788 entries, 0 missing, 0 mismatches** before the final evidence/report insertion; a new final manifest is generated when the release ZIP is rebuilt.
- Python compilation: PASS.
- WebUI JavaScript syntax (`node --check`): PASS.
- All three shipped YAML configs: PASS.
- v2.3.9 wheel clean install/import: PASS.

## Local browser UAT

The extracted package passed the seven-phase local Chromium UAT:

1. Data Map — PASS
2. Source Document Type — PASS
3. Target Document Type — PASS
4. Rule — PASS
5. Source Transport Profile — PASS
6. Target Transport Profile — PASS
7. BizFlow — PASS

BizFlow Edit → Save → Validate → Deploy also passed, final state `Deployed`.

SFTP-HAFT role defaults verified in the UAT:

- Sender / Dell / Source: `da-sender-sftphaft-dce-shared`
- Partner / Receiver / Target: `pt-receiver-sftphaft-dce-shared`

The UAT explicitly reports `dell_environment_contacted=false`; this is a local browser/runtime certification and not a claim that the package mutated the live Dell tenant.

## Governing execution rule

**Skill memory proposes. Current input supplies values. The live page authorizes every action.**
