# HIP Portal Agent V229 — Hybrid AutoWebGLM Form Execution

## Purpose

V229 fixes the live failure shown in the 2026-09-10 mission trace where a phase could observe/click controls, fill zero fields, then appear to hand off before the section judge blocked it. The fix is architectural: the phase-specific DDS form drivers no longer own a separate Playwright-only execution path.

The authoritative live action path is now:

1. **AutoWebGLM planner** receives task, current simplified DOM/viewport, action history and phase intent.
2. **HIP semantic action gate** proves the requested control belongs to the active surface and is the intended semantic control.
3. **BrowserSession canonicalizes the proven Locator** into a unique CSS selector valid for the current DOM generation.
4. **PyAutoGUI MCP is attempted first** when enabled/available and a trustworthy bounding box exists.
5. If PyAutoGUI is unavailable, fails, or does not establish the requested interaction, **Playwright MCP executes the exact canonical selector on the same authenticated browser session**.
6. If allowed and MCP execution is unavailable, **Python Playwright** is the final compatibility fallback.
7. **Exact post-action read-back/effect verification** checks the live control state/value.
8. **Authoritative provenance** is written into the state-graph attempt, Mission Trace and phase evidence.
9. A phase can pass only after its strict exact-completion checkpoint succeeds; section text/vision judges run after deterministic execution proof.

This is deliberately a hybrid executor. PyAutoGUI is preferred; Playwright is not treated as failure. The goal is to complete the portal task reliably rather than force a single interaction technology.

## Why V228 failed in the supplied live run

The V228 banner correctly described the *high-level* action ladder, but DDS field operations still had paths that could bypass `BrowserSession`. In particular, form-control helpers and batch fills could touch controls via Playwright directly without producing the same authoritative fill provenance expected by the Mission Trace/completion gate. That created the visible contradiction: clicks/observations existed, `filled=0`, and route progression could still be described as a handoff.

A second issue was selector translation. Some callers intentionally supplied a human-readable audit description such as `Data Maps top-right + Add`. That description is useful for logs and PyAutoGUI target context, but it is not CSS. If the action fell through to Playwright MCP, treating that human description as the MCP selector could fail even though the original Locator was correct.

V229 separates **audit label** from **dispatch selector** and makes form interaction provenance mandatory for live completion.

## Core implementation

### 1. Single governed DDS interaction broker

`hip_id_agent/dds_control_driver.py` now routes control actions through:

- `_broker_click(...)`
- `_broker_fill(...)`
- `_broker_press(...)`
- `set_text_control(...)`
- `set_text_controls_batch(...)`

On a live HIP page these functions delegate to the attached `BrowserSession`. They do not use a hidden raw `page.evaluate()` setter as a normal governed fallback.

The broker records:

- action type
- selector
- semantic/audit label
- success/failure
- actual executor
- whether the value was present
- whether the action is authoritative

An action is authoritative when it was successfully performed by the PyAutoGUI MCP, Playwright MCP or governed Python Playwright execution path. Phase-specific contract-aware file uploads are separately accepted as authoritative upload transactions.

### 2. AutoWebGLM is still used

AutoWebGLM is not removed or replaced by PyAutoGUI. It is the planning and intent-alignment layer. The physical executor is chosen only after the semantic control has been proven.

For governed DDS text controls, the driver calls AutoWebGLM intent alignment before dispatch, and BrowserSession revalidates immediately before physical execution. The execution provenance records:

- planner: `autowebglm-primary`
- planner status/alignment
- semantic control id
- semantic confidence/margin
- semantic revalidation status
- actual executor
- post-action semantic effect

### 3. Human description -> unique current-DOM CSS for Playwright MCP

`BrowserSession._mcp_safe_selector()` rejects plain human phrases as selectors.

`BrowserSession._canonical_selector_for_locator()` receives the already-proven Playwright Locator and tries, in order:

- the caller selector if it uniquely identifies the exact element
- element id
- stable attributes such as `data-testid`, `data-test`, `data-qa`, `data-cy`, `data-control-id`, `name`, `aria-label`, `title`, `placeholder`, `role`
- Dell DDS wrapper/child combinations such as `dds-button[kind=...][size=...] button[aria-label=...]`
- an immediate `nth-of-type` DOM path as a same-generation-only fallback

The generated path is **not promoted into long-term portal knowledge**. Angular/DDS can rerender it, so it is used only for the current action generation.

### 4. Hybrid physical execution

For clicks, fills/search and keys, `BrowserSession` uses this order:

**PyAutoGUI MCP -> Playwright MCP -> Python Playwright**

The default `config.yaml` therefore uses:

```yaml
pyautogui:
  enabled: true
  interaction_mode: "primary"
  primary_for_clicks: true
  primary_for_form_fill: true
  primary_for_search_fill: true
  primary_for_keys: true
  fallback_to_playwright_mcp: true
  fallback_to_python_playwright: true
  mcp_required: false
```

`mcp_required: false` means a missing PyAutoGUI MCP does not block a healthy Playwright MCP path. `config.mcp-required.windows.yaml` remains available when an operator explicitly wants PyAutoGUI MCP itself to be mandatory.

### 5. Mutation duplicate protection

Final tenant mutations are treated differently from safe form-entry/fill operations. Once a potentially mutating click is dispatched, an exception can mean the backend outcome is unknown. V229 preserves the mutation-outcome guard and will not blindly replay a possibly-successful mutation through another executor.

The no-save dummy/form-learning flow still blocks Save/Create/Submit/Delete/Deploy. API mutation remains separately governed by explicit confirmation and policy.

## Screenshot-derived Data Map understanding

`hip_id_agent/hip_surface_ground_truth.py` contains value-free supervised UI facts learned from the supplied Dell portal snippets.

### Listing surface

Expected semantic structure includes:

- Data Maps listing route
- page-level accessible `Add` button
- Dell DDS button host
- small/tertiary action-bar placement
- upper-right location as ranking evidence

Negative candidates include:

- Expand the row
- Filter
- Manage Columns
- Edit
- Clone
- Migrate
- Cancel
- Submit

### Create Map surface

The expected create form is a **same-route in-page drawer/modal**, with semantic evidence such as:

- `Create Map`
- Map Identifier
- Status
- Map Name
- Map Class
- Contivo version
- Map Data
- optional schema/cross-reference controls
- Cancel / Submit terminal controls

Existing-row DEV/TEST1/TEST2/PROD/Edit/Clone/Migrate content is negative evidence for the create surface.

No tenant ids, customer values, generated Angular ids or screenshot coordinates are stored in this ground truth.

### Status control correction

Data Map Status is no longer assumed to be a dropdown. If the live DDS control exposes `role=switch`, `role=checkbox`, checkbox type or a `dds-switch` host, it is handled as a boolean state control and verified from checked/ARIA state.

## Same-page/in-page form contract

The V227 route lock remains in effect for all governed sections:

- Data Map
- Source Document Type
- Target Document Type
- Rule
- Source Transport Profile
- Target Transport Profile
- BizFlow

Standard flow:

`listing -> page-level + Add -> same-route drawer/form -> discover controls -> map values -> brokered fill -> exact verify`

BizFlow adds its intermediate template/card selection before the tabbed form. Route/query/hash changes that are part of the same SPA surface can be accepted; a wrong create pathname is rejected when the learned contract says the form is in-page.

## Strict completion rule

`phase_exact_completion_checkpoint()` now requires the phase execution artifact to prove:

- execution pass/status is not failed/blocked
- no blocking failed attempts remain
- `fields_filled_or_verified == true`
- `exact_execution_verified == true`
- `authoritative_execution_verified == true`

For strict live execution, a value merely being visible in the DOM is not authoritative completion. At least one relevant writable field must have a proven broker transaction (or governed contract-aware upload) and exact verification.

Important fail-closed conditions include:

- `HIP_FORM_CONTROLS_NOT_DISCOVERED`
- `HIP_FORM_CONTROLS_NOT_BOUND`
- `HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED`
- `HIP_PHASE_EXACT_EXECUTION_NOT_VERIFIED`
- `HIP_PHASE_AUTHORITATIVE_INTERACTION_NOT_VERIFIED`

## Mission Trace correction

When a phase is blocked but the diagnostic run is allowed to visit later sections, `MissionTraceLedger.record_transition()` uses `continued_from_blocked`.

The source phase text becomes equivalent to:

`Route continued ... for diagnostics; phase still blocked`

It cannot be presented as `handoff verified`. This directly fixes the misleading state shown in the supplied failed-run screenshot.

## Browser-Use / WebUI role

Browser-Use remains a perception/recovery helper on the governed browser. The optional `browser-use/web-ui` sidecar is provided for isolated debugging/own-browser experiments, but it is not a second mutation authority competing with the main mission controller.

The primary production control loop remains AutoWebGLM + HIP semantic intelligence + hybrid PyAutoGUI/Playwright execution.

## Main files changed in V229

The important implementation files are:

- `hip_id_agent/browser_session.py` — canonical selector conversion, hybrid click/fill/key dispatch and provenance
- `hip_id_agent/dds_control_driver.py` — single broker for DDS controls; no bypassing batch form fill
- `hip_id_agent/stateful_form_runtime.py` — authoritative-executor proof in attempts and strict stage audit
- `hip_id_agent/dummy_fill_e2e.py` — phase exact-completion checkpoint
- `hip_id_agent/datamap_kb.py` — same-page Create Map proof, screenshot-derived semantics and Status switch handling
- `hip_id_agent/hip_surface_ground_truth.py` — stable value-free supervised portal facts
- `hip_id_agent/mission_trace.py` — blocked diagnostic continuation is not verified handoff
- `hip_id_agent/live_runtime_certification.py` — hybrid readiness semantics
- `hip_id_agent/config.py`, `config.yaml`, `config.example.yaml` — V229 hybrid defaults
- `backend/app.py`, `webui/app.js` — control-plane/runtime status updates
- `tests/test_v229_hybrid_interaction_completion.py` — direct V229 regressions

## Recommended Windows run

Use the normal hybrid profile first:

```powershell
cd <extracted-project>
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
bun install
```

Provide Dell AIA/model settings in `.env`, then certify the actual workstation/browser without requiring PyAutoGUI itself:

```powershell
python -m hip_id_agent.cli certify-live-runtime --config config.yaml --allow-missing-pyautogui-mcp
```

Run the full safe form mission:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config config.yaml `
  --customer UHAUL-POASN `
  --input-json .\examples\uhaul_poasn_full_dummy_input.json `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --runs-dir C:\hip_runs
```

`--require-mcp` in this command means the Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP control plane is required. With normal `config.yaml`, PyAutoGUI MCP remains preferred but optional.

Equivalent helper:

```powershell
.\run_live_full_dummy.ps1 -SkipInstall -SkipTests
```

To deliberately require PyAutoGUI MCP too:

```powershell
.\run_live_full_dummy.ps1 -Config .\config.mcp-required.windows.yaml -SkipInstall -SkipTests
```

## What must be checked on the next Dell live run

The first Data Map card should no longer look like `observed 2 / filled 0 / clicked 4` with a verified handoff. During a healthy create-form fill, inspect the phase execution JSON/Mission Trace for:

- active `Create Map` drawer proof
- discovered/bound expected controls
- one or more fill attempts with `authoritative_execution: true`
- `executor` equal to PyAutoGUI MCP or Playwright MCP/Python Playwright fallback
- `exact_verified: true`
- `execution_stage_audit.authoritative_execution_verified: true`
- exact completion checkpoint pass before section judge/handoff

If PyAutoGUI is unavailable, the run should visibly continue with Playwright MCP rather than block merely because PyAutoGUI did not start.
