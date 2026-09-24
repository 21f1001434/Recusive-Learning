# HIP Data Map Adaptive Runtime and KB Fix — 2026-07-16

## Reviewed run

`UHAUL-POASN-FULL-DUMMY-20260716-145139`

## Why the run stopped after Data Map

The browser did fill the principal Data Map values. The run was terminated by the strict section gate because three independent situations were incorrectly combined into one failure:

1. The requested Data Map already exists in the read-only Data Map inventory.
   - Identifier: `DELLCoXMLASNXX08C_U-HAUL`
   - Existing version: `1.0`
   - Map name: `DELLCoXMLASNXX08C`
   - Map class: `Transform_DELLCoXMLASNXX08C`
2. The agent guessed values for two optional schema-upload controls even though the input JSON did not specify those fields.
   - The live controls accept only `.xsd`, `.json`, `.edi`, and `.txt`.
   - The agent selected XML examples from the uploads directory.
   - HIP correctly rejected those files as unsupported.
3. The text and vision judges contradicted deterministic evidence.
   - The deterministic judge found the expected committed values.
   - The text judge claimed some of the same values were absent.
   - The vision judge returned issues where `expected` and `observed` were identical.

The old run is correctly classified as blocked because unsupported files created real portal validation errors. It should not be promoted as validated knowledge.

## Runtime corrections

### Existing-object resolution

Before opening the Create Map form, the runtime now queries the captured read-only Data Map inventory using the natural key:

- Map Identifier
- Version

An exact match creates an `existing_object_resolution` record with mode `reuse_existing`. A duplicate identifier message is non-blocking only when that exact inventory match exists and there are no conflicting facts.

The runtime may still open the form for safe structural exploration, but it does not treat the existing object as a failed creation attempt.

### Input-driven optional uploads

Optional schema files are no longer inferred from unrelated files in the uploads directory.

The agent uploads an optional file only when the current input explicitly supplies that value. Required files continue to be resolved from explicit input and compatible assets.

### Live file-control contract learning

For every upload control, the runtime reads and stores:

- semantic field identity;
- required/optional state;
- live `accept` attribute;
- normalized accepted extensions;
- multiple-file behavior;
- ARIA validation state;
- accepted or rejected result after upload.

The learned contract is written to:

`data_map/datamap_file_input_contracts.json`

It is also applied to the current in-memory plan and persisted to Portal Brain. Future runs therefore know the live upload constraints without relying on dynamic DDS IDs.

### Field-scoped upload verification

A file operation succeeds only if:

- the selected asset matches the live accepted extensions;
- the file control does not become invalid;
- there is no field-scoped unsupported-type message;
- the portal reports an accepted file state.

Filename visibility alone is no longer considered proof of a successful upload.

### Judge reconciliation

Deterministic, field-bound DOM evidence remains authoritative for exact committed values.

The text judge cannot overturn a deterministic match without field-specific contradictory evidence. Vision issues where expected and observed values are equal are removed as internally inconsistent.

Neither reconciliation can hide a real portal validation error. Unsupported files, unresolved required fields, and other blocking messages still stop the run.

### Fail-closed diagnosis

When a phase is blocked, the runtime writes:

- `<phase>/section_judge_block_diagnosis.json`
- `blocking_diagnosis.json`

The report separates:

- blocking portal validation;
- exact-value mismatches;
- repeatable-row mismatches;
- text-judge conflicts;
- vision-judge conflicts;
- existing-object reuse evidence.

## KB learning policy

- Judge-approved evidence can become validated knowledge.
- Real failures remain negative evidence.
- Optional-field absence is not a failure.
- Unsupported uploads are stored as negative control-contract evidence.
- Exact existing-object matches are stored as reusable object-resolution knowledge.
- Dynamic DDS IDs are not durable selectors.
- Live semantic control contracts update the same run before later actions execute.

## Verification

- Python compilation: passed
- Complete automated suite: 258 passed
- Exact existing-map reuse regression: passed
- Optional schema skip regression: passed
- Live accept-contract regression: passed
- Unsupported XML rejection regression: passed
- Text-judge contradiction reconciliation: passed
- Vision contradiction reconciliation: passed
- Blocking validation diagnosis: passed

## Honest status

This patch fixes the Data Map termination mechanism found in the supplied run. It has not executed an authenticated HIP run in this environment. The next no-save run must prove that Data Map passes or is safely reused and that execution advances to Document Types, Rule, Transport Profiles, and BizFlow.
