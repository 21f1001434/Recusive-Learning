# HIP Portal Agentic Runtime Self-Heal Loop

## Objective

The runtime now behaves as one closed-loop browser agent rather than a set of disconnected fillers, MCP adapters and judges.

For every error it follows:

```text
Plan from phase input + validated Portal Brain/Unified KB
→ Act using the safe browser/MCP tool layer
→ Observe URLs, active page, DOM, events, mutations, network, console and screenshot
→ Classify the failure
→ Choose a bounded repair from a strict allow-list
→ Reopen and replay only the interrupted phase
→ Require deterministic + Dell AIA text + vision judge approval
→ Continue to the next phase
```

## MCP and tool roles

- Official Playwright MCP: primary safe navigation and UI action executor.
- Chrome DevTools MCP: independent tab, URL, network, console and low-level browser evidence.
- Python Playwright: deterministic DDS/Angular fallback and exact state extraction.
- Portal Brain and Unified KB: page contracts, known parent-child dependencies, hard gates, negative evidence and validated recovery history.
- Dell AIA gpt-oss-120b: optional recovery-plan advisor constrained to the deterministic safe action allow-list.
- Text and vision judges: independent acceptance gates. The recovery planner cannot approve its own action.

## Failure classes

The self-heal controller distinguishes:

- authentication expiry;
- target route not committed;
- MCP surface drift;
- blocking overlays;
- active Create/Add surface lost;
- required semantic control not found;
- exact value not committed;
- multi-select set mismatch;
- repeatable-row count or scope mismatch;
- file upload mismatch;
- text/vision/deterministic judge conflict;
- transient navigation/network timeout;
- reporting/serialization error;
- unknown recoverable failure;
- unsafe or mutating action request.

## Safe repair actions

Only these actions are executable:

- reauthenticate in the same Chrome context;
- route to the requested phase;
- resynchronize both MCPs;
- clear transient dropdowns/drawers and reopen the phase;
- recover the current page and route;
- refresh observers/evidence and reopen;
- replay the phase from its exact phase-local input JSON;
- refresh evidence and rerun the judge;
- stop fail-closed.

The loop never clicks Save, Create, Submit, Delete, Deploy, Publish, Update, Confirm or other final mutations.

## Bounded-loop protections

Default policy:

```yaml
runtime_self_heal:
  enabled: true
  max_phase_attempts: 5
  max_total_repairs: 20
  max_repeated_failure_signature: 2
  use_aia_advisor: true
  aia_advisor_timeout_seconds: 20
  capture_evidence: true
  retry_unknown_once: true
  fail_closed: true
```

A repeated unresolved signature cannot loop forever. Unknown failures receive one safe replay. The final attempt never executes another repair.

## Knowledge promotion

A successful repair is initially stored as candidate recovery knowledge. It becomes validated recovery knowledge only after the repaired phase passes the normal independent judge.

Failed repairs are stored as negative evidence and cannot replace validated deterministic paths.

## Evidence

Each repair writes:

```text
runtime_self_heal/<phase>/attempt_<n>_<classification>/
  failure_evidence.json
  failure_surface.png
  self_heal_decision.json
```

The run also writes:

```text
runtime_self_heal/runtime_self_heal_policy.json
runtime_self_heal/runtime_self_heal_summary.json
<phase>/phase_execution_attempts.json
<phase>/self_heal_resolution.json
```

## Validation

- Python compilation passed.
- New self-heal regressions passed.
- Complete suite: 319/319 passed.
- No authenticated Dell HIP live rerun was performed in this environment.
