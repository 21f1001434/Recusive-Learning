# False Loading Detection + Autonomous Page Health Fix

## Root cause

The previous watchdog queried broad selectors such as `[aria-busy="true"]`, `[role="progressbar"]`, and `[class*="spinner"]` anywhere in the page. Dell DDS/Angular can leave these nodes mounted after the active form is already usable. The runtime therefore treated a passive DOM marker as a global blocking loader and waited even while a user could click and fill controls.

BizFlow also had a separate broad spinner wait with the same behavior.

## Corrected readiness model

The agent now classifies each loading indicator as either:

- **Hard blocking**: covers a meaningful part of the viewport or active form, intercepts pointer events, or fails hit-testing for the requested target.
- **Passive**: visible in the DOM but does not cover the active form and does not intercept the requested control.

The classifier uses:

- requested-control hit testing with `document.elementFromPoint`;
- overlay geometry and viewport ratio;
- active form/drawer coverage ratio;
- pointer-events state;
- fixed/absolute/sticky layer evidence;
- active enabled-control count;
- two consecutive hard-blocking observations before starting the timer.

A passive indicator produces an audit record and the action proceeds immediately.

## Autonomous page-health judge

Before each browser action, the runtime now makes a deterministic decision:

- `proceed`
- `wait`
- `route_recover`
- `reauthenticate`
- `observe`

Route drift is proactively sent to the ReAct navigation controller. Authentication drift is sent to the persistent-session SSO recovery. Only a confirmed hard blocker reaches the two-minute loading watchdog.

## Watchdog behavior

- Passive indicator: proceed immediately.
- One transient hard-blocking sample: reobserve; do not start timer.
- Confirmed hard blocker: wait normally.
- Same hard blocker for 120 seconds: capture evidence and refresh once in the same authenticated browser context.
- Hard blocker remains after refresh: bounded self-heal and fail closed.

## Additional agentic behavior

The runtime self-heal controller now recognizes `false_loading_marker` and can run `reassess_page_health` before selecting any more invasive recovery. Dell AIA may advise only from the existing safe action allow-list; independent judges still decide success.

## Evidence

Passive-marker decisions are written under:

`mcp_runtime/autonomous_page_health/passive_loading_*.json`

Ambiguous/non-proceed health decisions are written under:

`mcp_runtime/autonomous_page_health/health_decision_*.json`

## Safety

The change does not bypass a real overlay and does not click Save/Create/Submit/Delete/Deploy/Publish. Pointer-events are not modified by the central watchdog.
