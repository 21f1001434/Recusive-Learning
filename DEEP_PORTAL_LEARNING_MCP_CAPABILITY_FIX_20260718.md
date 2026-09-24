# Deep Portal Learning and MCP Capability Fix

## Objective

Make the HIP agent learn the portal as a changing application rather than only remembering selectors. The implementation covers Data Map, Source Document Type, Target Document Type, Rule, Source Transport Profile, Target Transport Profile and BizFlow.

## MCP decision

The runtime keeps the two existing MCP servers attached to the same persistent Chrome context:

- Playwright MCP: primary safe actions and semantic accessibility snapshots.
- Chrome DevTools MCP: independent DOM, network, console and performance evidence.

A third browser controller was not added because it could introduce a separate browser/tab/session and weaken the single-SSO contract. The existing MCPs are instead used through a dynamic capability broker: supported tools are discovered at runtime and optional learning tools are used only when present.

## Added learning channels

### Semantic page model

Before and after every phase attempt, the runtime captures:

- URL and title
- headings and tabs
- active forms, drawers and dialogs
- visible controls and semantic labels
- required, disabled, multiple and invalid states
- selected option labels
- validation attributes
- active alerts and validation messages

Dynamic element IDs are not treated as the primary identity. Controls receive stable semantic fingerprints.

### Accessibility model

Playwright MCP supplies an independent structured accessibility snapshot. It is compared with the exact DOM model instead of replacing it.

### DevTools model

Chrome DevTools MCP supplies:

- DOM snapshot
- network request inventory
- console evidence
- optional performance trace on retry attempts

The runtime dynamically checks whether the installed MCP version supports each tool.

### API contract learner

Captured network events are converted to value-free contracts:

- HTTP method
- normalized endpoint template
- status codes
- query-key names
- request JSON shape
- response JSON shape
- resource type and phase stage

Volatile numeric IDs, UUIDs and long token-like path segments are replaced with placeholders. Sensitive values are not used as learned API identity.

### Action-to-API causality

Browser actions are linked to the network request IDs triggered by those actions. This lets the Portal Brain learn which UI control causes which read-only portal/API transition.

### Validation-rule learner

The model learns required state and HTML/DDS constraints such as min, max, minimum length, maximum length and pattern, along with observed invalid state and messages.

### State-transition learner

The runtime stores:

- controls added or removed
- parent-child changes
- repeatable-row transitions
- URL transitions
- action results
- DOM transition records

This complements the existing parent-value branch explorer.

### Coverage and information gain

Safe visible controls that were not exercised in the judged path are placed in an exploration agenda. They are persisted as unresolved learning gaps for future controlled exploration. Unsafe final actions are excluded.

### Drift detection

Each page receives a semantic fingerprint. A new fingerprint that differs from previously validated fingerprints is stored as a revalidation-required drift event, not immediately accepted as truth.

### Storage-state learning

Only localStorage and sessionStorage key names are retained. Values, cookies, tokens and authorization headers remain excluded.

## Judge-gated promotion

- Deterministic pass + required text/vision judge pass: validated learning.
- Form success but judge not passed: candidate learning.
- Failed attempt: negative evidence.

Candidate or negative evidence cannot replace validated knowledge.

## Prompt-injection protection

GPT-OSS-120B planner, exploration analyst and runtime recovery advisor now treat all page text and accessibility snapshot content as untrusted data. Instructions embedded in portal content are ignored. The deterministic safe-action allow-list remains authoritative.

## Artifacts

Run level:

- `portal_learning/portal_learning_policy.json`
- `portal_learning/portal_learning_manifest.json`
- `portal_learning/mcp_learning_capability_matrix.json`

Per phase attempt:

- `portal_learning/attempt_NN/before_observation.json`
- `portal_learning/attempt_NN/after_observation.json`
- `portal_learning/attempt_NN/before_compact_page_model.json`
- `portal_learning/attempt_NN/after_compact_page_model.json`
- `portal_learning/attempt_NN/performance_trace_start.json` when supported and applicable
- `portal_learning/attempt_NN/performance_trace_stop.json` when supported and applicable
- `portal_learning/attempt_NN/portal_learning_summary.json`

## Verification

- Python compilation passed.
- New targeted portal-learning tests passed.
- Complete suite: 354 passed.
- No authenticated Dell HIP live rerun was performed in this environment.
