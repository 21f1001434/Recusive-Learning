# HIP Portal Brain — Long-Term Memory Implementation Verdict

## Verdict

Implemented a persistent, cross-run HIP Portal form brain. It is stored outside individual run folders and is used as the primary knowledge source when compiling deterministic plans.

The brain does not blindly trust every run. Knowledge is promoted through a three-tier trust model:

- **Validated:** phase/section verification passed and the required section judge passed.
- **Candidate:** the run passed with warnings or lacked complete judge evidence.
- **Negative evidence:** the run failed. Failed selectors/values are retained for recovery analysis but cannot overwrite validated knowledge.

## Runtime architecture

1. Bootstrap persistent brain from historical run evidence.
2. Merge current `input.json` with validated form knowledge.
3. Build a deterministic, topologically ordered plan.
4. Execute safe actions through official Playwright MCP.
5. Use Chrome DevTools MCP for state/network/console evidence.
6. Run deterministic DOM, Dell AIA text, and Dell AIA vision judges.
7. Repair the current section and re-judge.
8. Move forward only after PASS.
9. Promote successful observations into long-term memory.

## Persistent files

Default location:

```text
./data/hip_memory/portal_brain/
```

Files:

```text
manifest.json
run_index.json
phases/data_map.json
phases/source_document_type.json
phases/target_document_type.json
phases/rule.json
phases/source_transport_profile.json
phases/target_transport_profile.json
phases/biz_flow.json
```

Each phase memory stores:

- semantic field nodes;
- stable labels, names, roles, and locator strategies;
- generated DDS selectors as evidence only;
- dropdown option catalogues;
- input JSON paths;
- parent-value-child dependencies;
- repeatable-row effects;
- successful action order;
- validated/candidate/failure counts;
- source run history;
- unresolved exploration gaps.

## Important selector policy

Generated IDs such as `dds-form-field-123456789` are not promoted as primary locators. The brain prefers semantic locators:

1. section and row scope;
2. accessible role;
3. label;
4. name/placeholder;
5. stable selector;
6. generated selector only as evidence/recovery fallback.


## Plan is consumed during filling

The deterministic plan is no longer only written as evidence. Runtime fill loops consume it:

- Data Map uses brain action order for field filling.
- Document Type sorts controls by brain plan.
- Rule generic fields follow the brain plan after exact repeatable-row filling.
- Transport Profile dependency order comes from the brain plan.
- BizFlow controls are sorted by the section-specific brain plan; dedicated nested-row executors still enforce row and accordion behavior.
- Every executed attempt records its plan node, plan order, brain node, confidence, preconditions, and expected effects.

Official Playwright MCP is attempted first for text and dropdown actions. Python Playwright is retained only as the DDS fallback when MCP cannot operate the control.

## LLM and vision role

`gpt-oss-120b` is not allowed to invent the normal plan. It judges structured DOM/state evidence and advises recovery for unknown changes.

The vision model checks the current section screenshot against expected values and golden references. If vision is required but unavailable, the section remains blocked.

## Validation

```text
pytest -q
222 passed in 7.93s
```

A live authenticated Dell HIP Portal run is still required to validate the final behavior against the current portal release.
