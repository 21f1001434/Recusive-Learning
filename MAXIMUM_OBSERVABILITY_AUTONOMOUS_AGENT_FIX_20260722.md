# Maximum-Observability Autonomous HIP Agent

## Objective

Fill every input-driven HIP form from Data Map through BizFlow in the correct parent-child order, prove every required input-to-control mapping, retain enough evidence to diagnose live drift, and promote only independently judged deterministic trajectories.

## New evidence captured

Each autonomous attempt can now capture:

- visible and hidden controls;
- Angular `formcontrolname`, validity, pending and rerender state;
- DDS/ARIA ownership through `aria-controls` and `aria-owns`;
- mounted option inventory and selected-state evidence;
- fieldset, section, drawer and repeated-row ancestry;
- safe and potentially mutating action inventory;
- capture-phase click, pointer, input, change, focus, blur and key events;
- DOM mutation history for child mounting, validation and overlay transitions;
- resource and navigation timing without URL query strings;
- popup, overlay, alert and validation-message inventory;
- local/session storage key names only—never values;
- sanitized DOM structure with scripts, credentials and field values removed;
- Playwright MCP and Chrome DevTools MCP evidence;
- input-to-control coverage and deterministic replay readiness.

## Correctness gate

A phase is not eligible for deterministic memory promotion when any required actionable graph node lacks an input path, the dependency graph is cyclic, deterministic verification fails, or the local browser evidence is unavailable. The autonomous self-heal loop then explores the earliest unresolved dependency instead of promoting a partial path.

## Deterministic acceleration

After a successful phase, the agent persists a value-free trajectory containing selectors, roles, labels, dependency order, event sequence, rerender waits, overlay behavior, row identity and page fingerprint. Customer values are read from the current input JSON on every run.

## Safety

The collector is read-only. Save, Create, Submit, Delete, Deploy, Publish, Update, Remove, Enable, Disable and Confirm remain blocked.
