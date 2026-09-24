# HIP AgentQ Full E2E Input-Contract Hardening — 2026-08-08

## Objective

Recheck the Streamlit + AgentQ UI/API autonomous HIP package end to end and make the completion verdict fail closed unless the supplied `input.json` is fully represented in the corresponding HIP forms from Data Map through BizFlow.

## What the recheck found

The existing 504-test baseline was internally green, but a leaf-by-leaf audit found that some real input values were not represented by the executable state graph. The largest gap was BizFlow. A partial graph could therefore finish all of its known nodes while still silently omitting fields present in `input.json`.

The hardened implementation removes that possibility. The browser mission is blocked before Chrome opens unless every scalar input leaf for the active seven-phase mission is either:

1. bound to an executable or verification node, or
2. explicitly classified as a read-only/structural value that must be accounted for but is not mutable in the portal.

## Final input coverage

| Phase | Input leaves | Executable/verification paths | Explicit non-mutable accounting | Coverage |
|---|---:|---:|---:|---:|
| Data Map | 7 | 7 | 0 | 100% |
| Source Document Type | 31 | 29 | 2 | 100% |
| Target Document Type | 31 | 29 | 2 | 100% |
| Rule | 19 | 19 | 0 | 100% |
| Source Transport Profile | 15 | 15 | 0 | 100% |
| Target Transport Profile | 15 | 15 | 0 | 100% |
| BizFlow | 61 | 58 | 3 | 100% |
| **Total** | **179** | **172** | **7** | **100%** |

All seven dependency graphs are acyclic.

## BizFlow completeness fixes

The following values are now explicitly executed or verified rather than being silently skipped:

- Flow Details `Current Flow Version` — read-only display accounting.
- Flow Identifier row 1 and row 2 `Document Type Name (Version)`.
- Mapping Transformer disabled/default `Source Document Type` verification.
- Mapping Transformer `Add Rule if not Listed Above?` switch.
- Process-step numbers — repeatable-row order accounting.
- Enricher disabled/default `Document Type` verification.
- Enricher `Target File Name Config` switch.
- Enricher filename row `Part Number` for every row.
- Routing Rule `Status`.
- Routing Rule `Execute Always`.

The parent/child ordering remains mandatory. For example, a process-step type must commit and rerender before its conditional controls are resolved; Target File Name Config must commit before filename parts are executed.

## Document Type completeness fixes

The top-level `operation` input is now explicitly treated as an alias of the Document Identifier operation instead of appearing as an unmapped leaf.

Conditional Attribute `expression` values that are intentionally blank are explicitly accounted as an absent/blank conditional child. Non-blank expressions continue to require a real child control and exact verification.

## Cross-object consistency preflight

The mission now validates the references connecting the seven objects before opening Chrome. It verifies, among other relationships:

- Rule source Document Type -> Source Document Type object.
- Rule Mapping Identifier -> Data Map identifier/version.
- Source/Target Transport Profile document types -> corresponding Document Types.
- BizFlow business-flow name -> mission `profile_name`.
- BizFlow source/target Transport Profiles -> TP objects.
- BizFlow source/target applications -> TP partner names.
- Every Flow Identifier Document Type -> source Document Type.
- Mapping Transformer target Document Type -> target Document Type.
- Mapping Transformer Rule -> Rule object.
- Routing Document Type -> source Document Type.
- Routing action Target -> target Transport Profile.

A contradiction is reported as an input defect rather than being sent to the web self-heal loop.

## UI behavior

The Streamlit application has a dedicated **Preflight** tab. Start Mission is disabled when any of the following is false:

- seven-phase input contract passes,
- each phase has 100% input-leaf coverage,
- dependency graph is acyclic,
- required golden screenshots exist,
- required Data Map upload asset exists,
- API write confirmation is valid when write mode is selected.

The existing Mission, API payload/response, UI/API crosswalk, Evidence and Console tabs remain available.

## API behavior

The existing form API instrumentation remains enabled:

- API traffic on form open,
- API traffic during parent/child filling,
- redacted request payloads,
- response status/headers/body,
- UI-input-to-API-key crosswalk,
- blocked Create/Save/Submit payload capture in `capture` mode,
- explicit dual-confirmation requirement for real API write mode.

## Safety

No default mutation permission was added. In capture mode the portal's mutating UI request remains blocked at the browser routing layer. Save/Create/Submit/Delete/Deploy remain outside normal fill-and-verify execution.

## Verification

- Baseline before hardening: 504 tests passed.
- New completeness regressions: 8 tests.
- Complete source-tree suite after hardening: **512/512 passed**.
- Python `compileall`: passed.
- Static Streamlit preflight using the included UHAUL input/golden/assets: passed.
- Seven phase leaf coverage: **179/179 (100%)**.
- Dependency cycles: **0**.

Authenticated Dell HIP execution still has to be performed in the corporate Dell SSO browser session; this development environment cannot assert a live portal completion.
