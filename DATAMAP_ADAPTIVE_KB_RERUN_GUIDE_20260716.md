# HIP Data Map Adaptive KB Rerun Guide

## 1. Replace the project

Extract the complete corrected package over a clean project directory. Preserve your local `.env`, Dell credentials, browser profile, uploaded assets, golden screenshots, and `data/hip_memory/portal_brain` directory.

Do not copy `.pytest_cache` or `__pycache__` folders from an older project.

## 2. Optional local verification

```powershell
python -m compileall -q hip_id_agent tests
python -m pytest -q
```

Expected result:

```text
258 passed
```

## 3. Run the existing command

No input change is required for the two optional Data Map schema fields. They will remain blank unless explicitly provided by `input.json`.

Use the same full no-save command. Complete Dell SSO manually when requested.

## 4. Expected Data Map behavior

The phase should:

1. Capture the read-only Data Map inventory.
2. Find `DELLCoXMLASNXX08C_U-HAUL` version `1.0` as an exact existing match.
3. Record `mode: reuse_existing`.
4. Safely explore the Create Map form without final Create/Save.
5. Fill the explicit Data Map fields.
6. Upload the required JAR.
7. Leave optional input/output schema controls blank because they are not present in the input JSON.
8. Learn the live upload contracts from the portal.
9. Accept the duplicate identifier only as existing-object evidence.
10. Pass the phase only when no other blocking validation remains.
11. Continue to the next phase.

## 5. Evidence to inspect

Within the new run directory, check:

```text
data_map/existing_object_resolution.json
data_map/datamap_file_input_contracts.json
data_map/section_judge_gate.json
data_map/section_judge_block_diagnosis.json   # only when blocked
blocking_diagnosis.json                       # only when blocked
phase_verification_report.csv
full_dummy_fill_summary.json
```

Expected object-resolution content:

```json
{
  "found": true,
  "mode": "reuse_existing"
}
```

Expected schema-control behavior:

```json
{
  "required": false,
  "accepted_extensions": [".xsd", ".json", ".edi", ".txt"],
  "operation": "skipped_optional_unspecified"
}
```

## 6. When all values can vary

Values are taken in this order:

1. Current `input.json` explicit value.
2. Exact existing-object inventory match where reuse is valid.
3. Current same-run live control options and dependencies.
4. Judge-approved Portal Brain knowledge.
5. Unified KB canonical seed.

The agent does not invent an optional value merely because a similarly named file exists. When a new field or value appears, it is captured as candidate knowledge, applied to the same-run plan when safe, and promoted only after judge approval.

## 7. Safety

The run must continue to block final Save, Create, Submit, Delete, Deploy, Publish, Update, Enable/Disable, and Confirm actions.
