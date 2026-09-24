# HIP Browser Intelligence Platform — Discovery + Future-Task Milestone

## Objective

This milestone turns the HIP automation project from a seven-form configuration runner into a reusable browser-intelligence platform. The same AutoGen 0.7.5 / AgentQ / MCP automation core now learns what the HIP Portal can do, persists that knowledge as semantic capabilities, maps UI actions to observed APIs, and can plan and execute later user tasks against the learned portal model.

## Architecture split

The codebase is now divided into three explicit layers:

1. **Core (`hip_id_agent`)** — browser ownership, AgentQ, AutoGen 0.7.5, parent/child form execution, capability graph, API capture, safety and deterministic memory.
2. **Backend (`backend`)** — FastAPI mission lifecycle, discovery, capability/API queries and future-task planning/execution.
3. **Frontend (`frontend`)** — Streamlit client only. It does not import Playwright or own a browser.

## New HIP Capability Graph

Persistent location:

`data/hip_memory/portal_brain/capability_graph.json`

The graph stores value-free knowledge:

- page families and observed URL/fingerprint history;
- Search/filter controls;
- row expand/collapse controls;
- listing actions such as Edit, Clone, Migrate, Deploy, Delete, View/Details;
- Add/Create entry points;
- form controls learned from validated configuration trajectories;
- parent-before-child relations;
- selector candidates and accessible roles/names;
- observed API endpoint/method/status/shape contracts;
- UI action -> API causal relations;
- successful observation counts used for deterministic exploitation.

Customer/map/partner values are not promoted into reusable scope metadata. Row knowledge is stored structurally as `entity_row`, `page_surface` or `form_surface`.

## Learn HIP discovery mission

New CLI:

`python -m hip_id_agent.cli learn-hip ...`

The mission uses one authenticated Chrome session and visits:

- Data Maps
- Document Types
- Rules
- Transport Profiles
- BizFlows

For each page family it captures:

1. listing DOM/ARIA/control/action structure;
2. search capability and a safe current-input search example;
3. matching-row expansion when available;
4. all revealed row actions and their risk classification;
5. one safe Edit/View/Details surface when available;
6. Add/Create form entry and mounted form structure;
7. maximum-observability structural evidence;
8. request payload / response status / response body evidence;
9. API endpoint and payload/response shape contracts;
10. UI -> API causal relationships.

### Discovery safety

Discovery automatically executes only non-final structural/navigation interactions. Mutation-grade actions are inventoried but not executed.

Examples learned but not automatically invoked:

- Deploy
- Delete
- Migrate
- Publish
- Save / Submit / final Create

Clone is classified as a draft action and is inventoried rather than automatically invoked because different HIP pages may implement it differently.

## Configuration mission integration

The existing seven-phase configuration mission now promotes a phase into the same Capability Graph only after its strict assurance gate passes.

Validated form trajectories contribute:

- semantic field identity;
- stable selector candidates;
- section and row kind;
- event and wait profile;
- Angular rebind behavior;
- parent-child relation;
- validated page fingerprint;
- observed form API contracts.

This means **Learn HIP** and **Configure HIP** improve the same long-term portal model.

## Future Task Agent

New commands:

- `plan-future-task`
- `run-future-task`
- `capability-status`

A future task such as:

`Search data map "MAP_A", expand it, then Edit`

is converted to a capability-bound plan:

1. navigate to Data Maps;
2. use the learned search capability;
3. scope expansion to the row containing the current task entity;
4. use the learned Edit capability;
5. capture the APIs caused by the action;
6. update successful capability observations.

The executor tries learned selectors first, then semantically rebinds using row scope, role, accessible name, label and placeholder. Dynamic DDS IDs are therefore evidence, not permanent truth.

### Mutation authorization

A future Deploy/Delete/Migrate-type task requires all three gates:

1. `--allow-portal-mutation`
2. environment variable `HIP_ALLOW_PORTAL_MUTATION=YES`
3. exact confirmation phrase `ALLOW HIP MUTATION`

The browser mutation authorization contains only the exact planned action labels and is cleared after the task.

## API intelligence for future tasks

Future task execution records the request/response transactions caused by each capability and promotes value-free API contracts to the graph. The request ID, method, URL, response status, request payload (redacted), response payload (redacted) and causing capability are retained in run evidence.

## Backend API

FastAPI routes include:

- `GET /health`
- `GET /api/runtime/status`
- `POST /api/discovery/start`
- `GET /api/discovery/status`
- `POST /api/discovery/stop`
- `GET /api/capabilities/pages`
- `GET /api/capabilities`
- `GET /api/capabilities/{capability_id}`
- `GET /api/apis`
- `POST /api/future-task/plan`
- `POST /api/future-task/run`
- `GET /api/runs`

## Streamlit frontend

Tabs:

- **Learn HIP**
- **Portal Knowledge**
- **API Explorer**
- **Future Task Agent**
- **Runs / Console**

The frontend only calls FastAPI. Browser execution remains in the backend/core process.

## AutoGen and MCP runtime

Autonomous discovery/future-task execution requires:

- `autogen-agentchat==0.7.5`
- `autogen-core==0.7.5`
- `autogen-ext[openai]==0.7.5`
- Playwright MCP
- Chrome DevTools MCP
- HIP Intelligence MCP

The CLI fails closed before portal execution if AutoGen 0.7.5 is not installed/importable.

## Test result

Final source-tree regression inventory: **541 / 541 passed**.
