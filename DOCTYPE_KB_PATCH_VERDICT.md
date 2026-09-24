# Document Type KB Code Review & Patch Verdict

## Verdict

**Fixed and ready for next live Document Type KB run.**

The submitted Document Type KB run was useful, but the generator code still had a few Data Map leftovers and missed some Document Type-specific details from the actual KB evidence.

## Issues found in the uploaded KB/code comparison

1. **Data Map wording leaked into Document Type output**
   - `DOCTYPE_KB_SUMMARY.md` used wording like `Numeric map IDs found`.
   - The Knowledge Graph debug question still referenced `mapClass`.

2. **Document Type listing fields were not fully normalized**
   - The portal summary rows contain fields like:
     - `dataFormatType`
     - `transactionType`
     - `validationType`
     - `availableEnvironments`
   - The old code did not preserve all of them cleanly in inventory/CSV/lookup output.

3. **Nested `document_identifier` payload was being stringified**
   - Input like:
     ```json
     {
       "operation": "All conditions are satisfied",
       "rows": [{"derived_from": "TRANSACTION_ROOT_ELEMENT", "value": "DellAutoASN"}]
     }
     ```
   - was being converted into a long raw dict string for dummy fill.
   - Fixed to extract:
     - `document_identifier = DellAutoASN`
     - `document_identifier_operation = All conditions are satisfied`
     - `document_identifier_derived_from = TRANSACTION_ROOT_ELEMENT`

4. **Form field mapping was wrong for attributes**
   - `Attribute Name` was incorrectly mapped to `document_type_name`.
   - `Usage`, `Operation`, `Derived From`, and `Validation Type` were not mapped as Document Type-specific fields.

5. **Detail API environment parameter was malformed**
   - Existing output showed values like `environment=%5B%27DEV%27...` from stringified list values.
   - Fixed to normalize `['DEV', 'TEST1']` to `DEV`.

6. **Dropdown option capture was noisy**
   - Transaction Type dropdown captured footer items such as `Copyright`, `Privacy`, `Terms of Use`.
   - Fixed option collection to ignore footer/cookie noise and only collect visible option/menu/listbox items.

7. **Final ID completion report count could become stale**
   - Uploaded KB showed inventory rows and report totals not fully aligned after final evidence merge.
   - Fixed final report recalculation after all evidence sources are merged.

## Code changes made

- `hip_id_agent/doctype_kb.py`
  - Added robust scalar extraction for nested Document Type payloads.
  - Added full Document Type field normalization for `transactionType`, `dataFormatType`, `validationType`, `description`, and document identifier parts.
  - Corrected field mapping for Document Type Add form controls.
  - Fixed dropdown capture filtering.
  - Improved DDS/Angular dummy value setting with Playwright fill + native setter fallback.
  - Corrected Knowledge Graph and Markdown summary wording.
  - Recalculated final ID completion report after final merge.
  - Expanded CSV inventory output with Document Type-specific columns.

- `tests/test_doctype_kb.py`
  - Added tests for Document Type-specific API fields.
  - Added tests for nested document identifier extraction.
  - Added tests for Attribute field mapping.
  - Added test for environment list normalization.

## Local verification

```text
117 passed
```

Command used:

```powershell
pytest -q
```

## Expected improvement in next run

The next `discover-doctype-kb` run should produce a cleaner KB with:

- Correct Document Type wording instead of Data Map wording.
- Better inventory columns: `transaction_type`, `format`, `validation_type`.
- Clean `document_identifier` extracted from nested payloads.
- Better required-field mappings for Operation / Derived From / Attribute Name / Usage / Validation Type.
- Cleaner dropdown options without footer links.
- More accurate final ID completion report totals.

