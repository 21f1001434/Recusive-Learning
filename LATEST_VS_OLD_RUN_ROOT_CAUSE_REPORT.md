# HIP Portal Latest-vs-Old Run Root-Cause Report

Date: 2026-07-16

## Evidence reviewed

- Old run: `UHAUL-POASN-FULL-DUMMY-20260710-033135`
- Latest run: `UHAUL-POASN-FULL-DUMMY-20260716-124137`
- Current source baseline: `hip_portal_e2e_adaptive_self_healing_kb_explorer_judged_full_code (2).zip`

## Executive finding

The latest run did **not** demonstrate a Data Map fill failure. It filled the Data Map and produced a valid post-fill screenshot, DOM snapshot, seven successful fill/upload attempts, and a passing phase verification. Execution stopped because the section judge was given incomplete evidence and the text model was incorrectly reused as a vision model.

The old run exposed additional issues that the latest run never reached:

1. Source and target Transport Profile screenshots existed but were omitted from the phase verification registry, causing false failures.
2. BizFlow had genuine missing dependent controls in Target Details and Configure Routing.
3. The U-HAUL example used `dce-default-sender/receiver`, while the reviewed golden contract requires `dce-shared-sender/receiver`.

## Latest run: July 16

### Observed status

- Overall: `blocked_by_section_judge`
- Blocked phase: `data_map`
- Phase verification: Data Map `pass`
- Data Map attempts: 7 successful
- Screenshot: present
- Deterministic post-fill replay after this patch: **pass**

### Root cause

`judge_artifact_section()` previously supplied the text judge with:

- expected input;
- counts;
- warnings;
- an attempt sample.

It did not supply the final field-bound DOM values. Consequently, the model reported all actual values as null even though the saved DOM contained `data-hip-locked-value` attributes and the fill plan contained successful field-bound attempts.

The separate vision verification path also fell back to `MODEL_NAME=gpt-oss-120b`. This produced an apparent visual pass even though the section vision judge said the images were inaccessible. The two vision paths therefore contradicted each other.

### Fix

- Reconstruct post-fill state from the final saved HTML and latest successful semantic attempts.
- Require exact field-bound values; do not accept page-wide text or substring matches.
- Supply the same actual state to deterministic and text judges.
- Require an explicit multimodal deployment.
- Run a real two-pixel vision capability probe before opening the portal.
- Use the same strict multimodal configuration for section and optional vision verification.

## Old run: July 10

### Phase results

| Phase | Old result | Correct interpretation after patch |
|---|---:|---|
| Data Map | Pass | Genuine pass |
| Source Document Type | Pass | Genuine pass |
| Target Document Type | Pass | Genuine pass |
| Rule | Pass with warnings | Filled; dropdown option capture incomplete |
| Source Transport Profile | Failed | Screenshot registry false negative; screenshot exists; no failed attempts |
| Target Transport Profile | Failed | Screenshot registry false negative; screenshot exists; no failed attempts |
| BizFlow | Pass with warnings | Not acceptable: seven real dependent-field failures |

### Genuine BizFlow failures

Target Details, process row 1:

- Action = `Mapping` not visible/fillable.
- Target Document Type not visible/fillable.
- Rule not visible/fillable.

Configure Routing:

- First row Attribute Name/Unit was not visible.
- Second row Operator, Value and Attribute Name/Unit were not visible.

### Current runtime handling

The current code now:

- expands each Process Step accordion;
- requeries controls after selecting Action;
- scopes target document type, rule and mapping controls to the active Process Step;
- creates nested rows by effect-based exact row count;
- regroups unlabeled DDS controls by row geometry;
- refills each routing row using row index;
- retries the same section through the section judge before progression.

The July 16 run stopped before exercising these paths, so they still require an authenticated rerun.

## MCP decision

No third general-purpose MCP server was added. A third browser or filesystem server would not solve these field-state problems and would expand the mutation/security surface.

The existing MCP stack was strengthened instead:

- Playwright MCP remains the primary action executor.
- Chrome DevTools MCP is now launched with the same CDP browser URL.
- Experimental page-ID routing is enabled.
- The expected HIP page is selected explicitly.
- A same-surface gate compares the local page with Chrome DevTools MCP and captures a Playwright MCP snapshot.
- Playwright MCP exact-value verification is required after MCP field fills.
- Chrome DevTools MCP remains available for console, network and snapshot evidence during repairs.

## Input correction

The runnable U-HAUL example now uses:

- Source deployment group: `dce-shared-sender`
- Target deployment group: `dce-shared-receiver`

A generic `_live_acceptance_contract` preflight prevents reviewed values from silently drifting.

## Local verification

- Compilation: passed
- Automated tests: 237 passed
- Replayed July 16 Data Map artifact through patched deterministic judge: passed
- Replayed old source TP screenshot verification: passed
- Replayed old target TP screenshot verification: passed

## Remaining live proof

This environment cannot authenticate to the Dell HIP Portal. The patch is locally verified, but a new authenticated no-save run is still required to prove the current portal behavior for Rule, both Transport Profiles and all BizFlow sections.
