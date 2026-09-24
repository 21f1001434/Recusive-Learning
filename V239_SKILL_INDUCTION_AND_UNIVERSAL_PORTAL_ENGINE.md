# V239 — Skill Induction and Universal Portal Engine

## Goal

The agent is no longer restricted to a fixed list of HIP forms. A user can request an activity that the currently accessible web application offers. The runtime learns the current page/action/form semantics from live evidence, executes only actions that are semantically proven on the live page, fills business values only from the current `input.json`, and converts successful verified trajectories into reusable semantic skills.

## Runtime contract

1. **Understand the request** — parse known actions and target areas, and keep an adaptive live-goal path for unknown actions.
2. **Retrieve a skill** — search validated induced skills using task/action/target/input-root similarity and freshness-aware effective confidence.
3. **Instantiate, never copy** — a matched skill is rebuilt using the current start URL, current entity and current input file. Stored customer values and environment URLs are never reused.
4. **Live re-proof** — every navigation/action/form control must be resolved against the current DOM/accessibility/foreground surface before dispatch.
5. **Exact form completion** — every applicable nonblank runtime-input leaf must be uniquely bound and exact-readback verified.
6. **Govern mutations** — create/save/deploy/delete/migrate/approve/etc. remain behind the explicit mutation gate. Skill memory cannot broaden mutation permission.
7. **Self-heal** — if a non-mutating skill step drifts, lower skill confidence and restart through adaptive live discovery. Do not blindly retry uncertain mutation outcomes.
8. **Induce from what actually worked** — after a successful exact-verified run, save the executed value-free workflow and semantic form blueprint as a skill.
9. **Observe asynchronously** — emit MLflow telemetry without putting MLflow on the action authorization path.

## What an induced skill stores

- ordered workflow step types and semantic action labels;
- page-family context;
- input JSON paths and semantic field identity;
- section/row relationships;
- verification policy;
- success/failure counts, confidence, freshness and drift status.

## What it never stores

- business/customer values;
- passwords/secrets;
- environment-specific URLs from the learned run;
- CSS selectors or XPath;
- transient browser/framework ids;
- screen or viewport coordinates;
- bounding boxes.

## Skill lifecycle

`successful exact execution -> candidate/validated skill -> matched future task -> live-reproof replay -> reinforce on success`

`live contradiction -> confidence reduction -> repeated contradiction -> drift_suspect -> adaptive rediscovery -> re-induction`

## Key files

- `hip_id_agent/skill_induction.py`
- `hip_id_agent/universal_portal_operator.py`
- `hip_id_agent/capability_graph.py`
- `hip_id_agent/stateful_form_runtime.py`
- `hip_id_agent/mlflow_async.py`
- `backend/app.py`
- `webui/index.html`
- `webui/app.js`
- `tests/test_v239_universal_skill_induction.py`
