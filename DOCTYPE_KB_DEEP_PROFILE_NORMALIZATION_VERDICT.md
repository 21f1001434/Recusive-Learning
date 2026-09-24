# Document Type KB Deep Profile Normalization Fix Verdict

## Verdict
Accepted. The latest portal run completed the deep-profile phase, but the reviewed output showed one normalization gap: the details API returned `documentIdentifier.attributeList[]` and `documentIdentifier.operator`, while the normalized KB fields only understood `documentIdentifier.rows[]` / `operation`.

## Fixed
- Normalizes `documentIdentifier.attributeList[]` into `document_identifier_rows`.
- Maps `documentIdentifier.operator` into `document_identifier_operation`.
- Maps first identifier row into:
  - `document_identifier`
  - `document_identifier_derived_from`
  - `document_identifier_rows[].expression`
- Preserves the raw details payload under `raw_detail_compact`.
- Adds regression test for the live details API shape.

## Validation
```text
124 passed
```

## Result
The next full run will produce deep-profile reports where identifier rows are counted correctly instead of showing `with_document_identifier_rows: 0`.
