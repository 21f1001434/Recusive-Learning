# Assured AgentQ Full E2E Hardening — 2026-08-12

## Objective

The autonomous HIP agent must read the current `input.json`, configure every supported form in the correct parent-child sequence, verify each committed state, capture the useful UI/API evidence produced by the same authenticated Chrome session, self-heal when the portal drifts, and only then learn a deterministic replay path for later fast runs.

## Final phase scope

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

The existing full-input contract remains fail-closed: all 179 scalar leaves in the current UHAUL input are accounted for across execution, verification, structural/read-only expectations, or conditional-blank semantics.

## New hardening added in this build

### Autonomous mission now owns the complete stack

`--autonomous-mission` now automatically forces:

- all seven phases;
- unlimited safe runtime self-heal;
- AgentQ crawler fusion;
- dual UI/API capture;
- submit-request interception;
- required API evidence;
- all three MCPs;
- forensic evidence;
- maximum observability;
- heavy evidence;
- default UHAUL golden screenshots when present;
- strict phase mission assurance before deterministic-memory promotion.

This removes the risk that a caller forgets one of the flags required for the intended architecture.

### Fresh three-MCP quorum

After a phase reaches its exact UI state and passes the independent judges, the runtime recaptures fresh read-only evidence from:

- Playwright MCP: current URL and accessibility snapshot;
- Chrome DevTools MCP: current URL and DOM snapshot;
- HIP Intelligence MCP: fresh structural representation.

The quorum is taken at the end of the current attempt, not reused from startup. A strict autonomous phase cannot be promoted when any required MCP channel is unavailable, stale, or attached to another surface.

### Mission assurance promotion gate

Before a phase enters deterministic long-term memory, all of the following must pass:

- deterministic UI exact-state verification;
- text/vision section judge;
- 100% input/control observability coverage;
- deterministic replay readiness;
- UI/API capture gate;
- fresh MCP quorum;
- validated deterministic trajectory fingerprint;
- parent-child contract fingerprint;
- golden reference evidence.

A failed assurance dimension returns the phase to the self-heal loop instead of allowing partial knowledge promotion.

### Resume is assurance-aware

A prior run created under the assured-autonomous profile is resumable only when the phase also contains a passing `phase_mission_assurance.json`. Older proof formats cannot bypass the new assurance requirement for assured runs.

### Exact UI → API causality

Browser actions now record the exact set of CDP request IDs created during the action. This replaces the previous "last 10 network events" heuristic.

Each phase writes `ui_api_causal_trace.json`, connecting actions such as selecting a parent dropdown or adding a row with the network calls actually triggered by that interaction.

### Redacted HAR and API error ledger

Each phase now additionally writes:

- `redacted_network.har.json` — compact HAR-like request/response archive with authorization, cookies and query values redacted;
- `api_error_ledger.json` — HTTP >=400, request failures and response-body capture failures;
- `ui_api_causal_trace.json` — browser action to API transaction correlation.

Existing payload/response bundles, OpenAPI, Postman and crosswalk outputs remain available.

### Required response evidence

When `--require-api-capture` is active, a phase must have observed API contract evidence and at least one captured response status. A request-only trace no longer satisfies the required API capture gate.

### Configuration correctness

The YAML API option names now exactly match the Pydantic configuration fields:

- `build_ui_api_crosswalk`
- `replay_observed_validation_requests`

This removes silent configuration ignore risk.

## Existing high-value evidence retained

The package already captures and continues to use:

- visible and hidden controls;
- Angular validity/pending state;
- DDS `aria-controls` / `aria-owns` relationships;
- parent-child topology;
- repeated-row physical identity;
- click/input/change/focus/blur/keyboard timelines;
- DOM mutation timelines;
- performance traces on retry;
- local/session storage key names only, never values;
- network and console evidence;
- sanitized DOM structure;
- golden-state screenshots;
- Dell AIA text reasoning and judge;
- Gemma vision judge;
- Playwright MCP and Chrome DevTools MCP evidence;
- HIP Intelligence MCP actor/critic memory;
- Portal Brain and Flow Pattern Memory;
- deterministic trajectory fingerprints.

## Safety

Normal UI execution still does not send Save/Create/Submit/Delete/Deploy. In API capture mode, the visible final button can be exercised only behind a routing barrier that aborts mutating requests before Dell receives them. API write mode remains a separate two-key opt-in (`--allow-api-mutation` plus `HIP_ALLOW_API_MUTATION=YES`).

Authorization headers, cookies, browser profile data and long-term customer values are not persisted in the evidence or replay memory.

## Local verification

- Existing baseline: 512 tests.
- New assurance/causal/configuration regressions: 10 tests.
- Final source-tree result: **522/522 passed**.
- Python compileall: passed.
- Current UHAUL input preflight: passed.
- All seven phase input coverage values: 100%.
- Streamlit static mission preflight: passed.
- Generated mission command includes autonomous mission, AgentQ fusion, dual UI/API, submit capture, required API capture, required MCPs, maximum observability and until-complete self-heal.

A real authenticated Dell HIP completion still requires the operator's Dell SSO/private portal session; that cannot be certified from this environment.
