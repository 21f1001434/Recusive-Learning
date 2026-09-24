# Two U-HAUL Full-Dummy Run Evidence Review

**Verdict:** Both runs failed end-to-end. Correct findings were added as guarded KB evidence; neither run is a golden success.

## Run provenance

| Run | Overall | Mode | Observed backend | Vision judge | Files |
|---|---|---|---|---|---:|
| UHAUL-POASN-FULL-DUMMY-20260710-025930 | failed | fast_form_only | chrome-devtools-mcp | not_configured | 358 |
| UHAUL-POASN-FULL-DUMMY-20260710-033135 | failed | fast_form_only | chrome-devtools-mcp | not_configured | 358 |

> These runs demonstrate Chrome DevTools MCP availability, not Playwright MCP execution. No Save/Create/Submit/Delete/Deploy action was used.

## Phase verdicts

| Phase | Reported | Accepted verdict | Evidence |
|---|---|---|---|
| Data Map | pass in both run reports | PARTIAL EVIDENCE ONLY — not a valid create success | Create form was active and the JAR filename was visible, but Map Identifier already exists. Both XML files supplied to Input/Output Schema Validation were rejected as disallowed file types. |
| Source Document Type | pass in both run reports | REJECTED — false-positive verifier result | All 23 dummy-fill attempts recorded filled=false. The after-fill screenshot and DOM are the Document Types listing, not the active Create Document Type form. |
| Target Document Type | pass in both run reports | REJECTED — false-positive verifier result | All 23 dummy-fill attempts recorded filled=false. The after-fill screenshot and DOM are the Document Types listing, not the active Create Document Type form. |
| Rule | pass_with_warnings in both run reports | REJECTED AS COMPLETE — useful negative evidence retained | Rule Name already exists. The Receiver row was incomplete because Attribute Name/Unit was not found; the second Sender/Contains/DELL row was not created; Mapping Identifier was not found; an Operator control showed No options found. |
| Source Transport Profile | failed in both run reports | PARTIAL FIELD/SURFACE EVIDENCE — not create success | The create form showed the System identity and most downstream fields, but the profile already exists, the run used superseded dce-default-sender, Splitter Required was unverified, and the strict gate said no screenshot although a PNG existed. |
| Target Transport Profile | failed in both run reports | PARTIAL FIELD/SURFACE EVIDENCE — not create success | The create form showed Partner identity and core fields, but the profile already exists, the run used superseded dce-default-receiver, File Filtering Pattern and Post Transfer Action were not visible, Splitter Required was unverified, and the screenshot was not registered by the strict gate. |
| BizFlow | pass_with_warnings in both run reports | REJECTED — wrong active surface and incomplete rows | The artifact named as BizFlow after-fill is visibly a Create Rule form. The BizFlow DOM contains Content not found and stale Create Rule content. Process-step and routing row-specific controls were missing. |

## Knowledge added
- **RUN-KB-001 — Active-surface identity gate:** A phase can pass only when page title, object family, create/edit surface, wizard tab and active overlay/root all match the planned phase.
- **RUN-KB-002 — Exact-value completion contract:** Control discovery, attempt counts and screenshot presence do not prove completion. Required values must be read back from committed DOM/application state and compared exactly to input.json.
- **RUN-KB-003 — Validation-message gate:** Duplicate-name, disallowed-file, No options found, Content not found and required-field messages are blocking until resolved or explicitly classified as safe non-create exploration.
- **RUN-KB-004 — Upload acceptance gate:** A filename visible beside a file input proves selection only. Success requires no validation error and, when available, network/API acceptance evidence.
- **RUN-KB-005 — Planner-only events are excluded from fill success:** An LLM/AutoGen planning event can be successful while filled=false. Planner-only attempts must not increase field-fill or phase-success counts.
- **RUN-KB-006 — Backend provenance lock:** These runs prove Chrome DevTools MCP availability, not Playwright MCP execution. A Playwright-required run must fail preflight unless the observed backend is Playwright MCP.
- **RUN-KB-007 — Runtime selector IDs are ephemeral:** DDS form-field selectors changed across nearly every matched field between runs. Persist semantic field signatures and re-resolve selectors each run.
- **RUN-KB-008 — Evidence registry integrity:** The artifact manifest, filesystem, phase summary and judge must agree. A screenshot existing on disk but reported missing is an evidence-pipeline defect, not a trustworthy phase result.
- **RUN-KB-009 — Vision judge availability is explicit:** Both runs recorded vision verification as not configured. No phase may claim vision-backed approval when the vision endpoint/token was unavailable.
- **RUN-KB-010 — Safe form-only exploration is preserved:** The runs correctly avoided Save/Create/Submit/Delete/Deploy actions. This is useful exploration evidence but must remain clearly separate from create/deploy success.

## Selector drift

| Phase | Matched selectors | Changed |
|---|---:|---:|
| Data Map | 7 | 7 |
| Source Document Type | 23 | 23 |
| Target Document Type | 23 | 23 |
| Rule | 9 | 9 |
| Source Transport Profile | 16 | 13 |
| Target Transport Profile | 14 | 11 |
| BizFlow | 54 | 54 |

## Explicitly not promoted
- dce-default-sender / dce-default-receiver (superseded by dce-shared-sender / dce-shared-receiver)
- Private Existing Account Name values
- Runtime-generated dds-form-field numeric IDs
- Document Type pass verdicts from these runs
- Rule/BizFlow pass_with_warnings verdicts from these runs
- Any Create/Deploy success claim
- Any vision-backed success claim
- Any claim that Playwright MCP executed these runs
