# Data Maps Deep Capability Learning Milestone — 2026-08-25

## Objective
Turn Data Maps from a generic learned page into a deeply modeled HIP capability family that can be explored once, verified, and then replayed deterministically for future natural-language tasks.

## Implemented capabilities

### Listing intelligence
- Search control discovery and exact-entity search using the current input JSON only at runtime.
- Listing filter discovery; dropdown options are inspected but filters are not changed during learning.
- Pagination discovery for Next/Previous/page controls, including one safe transition and restore when available.
- Row-scoped expansion of the exact searched Data Map.

### Row action intelligence
The learner inventories all revealed row actions and classifies them by risk.

Safe draft/read surfaces are opened and closed without saving:
- Edit
- Clone/Copy
- View/Details
- History
- Audit

Mutation-grade actions are probed only behind a hard Playwright route barrier:
- Migrate
- Deploy
- Delete

During a mutation probe, POST/PUT/PATCH/DELETE requests and mutation-token GET requests are aborted before backend delivery. The learner captures the resulting dialog/surface, required controls, confirmation/cancel actions, request payload shape, endpoint and browser/API evidence.

## State and form learning
Every explored action records:
- pre-action structural fingerprint
- post-action structural fingerprint
- controls/actions added or removed
- drawer/dialog appearance
- structural form contract
- required controls
- role/label/framework selector candidates
- DOM/ARIA state through maximum observability
- AgentQ representation and trajectory outcome
- UI-caused API request/response evidence

## Deterministic replay memory
Capability Graph schema is upgraded to `hip.capability-graph.v3` with `replay_profiles`.

A replay profile stores only structural knowledge, for example:
1. navigate to Data Maps
2. search using `current_task.entity`
3. expand `matching_entity_row`
4. invoke the learned Edit capability
5. wait for the resulting surface to settle

Customer/map values are not persisted in replay memory.

Verified read/draft replay profiles are automatically selected by the Future Task Planner as `deterministic_replay`. If a learned selector drifts, the existing semantic row/role/label rebinding remains the fallback.

## Backend/frontend
New CLI command:
`python -m hip_id_agent.cli learn-datamaps-deep ...`

New FastAPI routes:
- `POST /api/datamaps/deep/start`
- `GET /api/replay-profiles`

Streamlit Learn HIP tab now has **Deep Learn Data Maps** and Portal Knowledge displays deterministic replay profiles.

## Safety
The discovery mission does not save/create/deploy/migrate/delete objects.
Safe mutation probing temporarily authorizes only the clicked label after the request-abort route is installed; authorization is cleared immediately after the probe.
Actual future mutation execution still requires the separate three-factor user authorization gate.

## Validation
- New focused + platform tests: 20/20 passed.
- Complete regression inventory: 548/548 passed in bounded chunks.
- Python compilation: passed.
