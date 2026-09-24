# Runtime Fix After UHAUL-POASN 20260709-210748

## Evidence reviewed

Latest run evidence shows:

- Data Map: pass
- Source Document Type: pass
- Target Document Type: pass
- Rule: pass_with_warnings only because several dropdown option inventories were empty
- Source Transport Profile: failed only because screenshot list was empty in verification, while the actual PNG exists under the TP KB folder
- Target Transport Profile: failed only because screenshot list was empty in verification, while the actual PNG exists under the TP KB folder
- BizFlow: pass_with_warnings with 66 field steps, but 5 failed attempts remained:
  - Configure Target / Process Step row 1 Action
  - Configure Target / Process Step row 1 Rule
  - Configure Target / Process Step row 2 Action
  - Configure Routing Operator transient failure later restored
  - Configure Routing Actions Target

## Fixes implemented

### 1. BizFlow section-scoped nested field selection

Added geometry/section-aware control discovery so nested rows are filled only inside their correct section:

- Configure Source -> Flow Identifier / Attribute rows
- Configure Target -> Process Step rows
- Configure Routing -> Actions rows

This prevents generic labels like `Type`, `Name`, or `Target` from binding to parent controls such as `Target Type` or `Target Transport Profile`.

### 2. Configure Target Process Step dependency re-scan

Process Step fields now re-scan after `Process Step Type` is selected because Action, Rule, Mapping Identifier, and Target Document Type can render after the type selection.

### 3. Configure Routing Action Target dependency re-scan

Routing Action Target now re-scans only inside the Actions section after Action Type is selected as `Route Document`.

### 4. Stop Target Details post-dependency overwrite

The old post-dependency target fill could run while still on Target Details and overwrite Target Type with the target transport profile. That is now restricted to Configure Routing / Actions only.

### 5. Sticky value conflict protection

Sticky restore now detects selector conflicts. If two different semantic fields try to lock different values to the same selector, it removes the lock instead of restoring a wrong value. This directly addresses “filling and unfilling.”

### 6. Strict combobox verification

Combobox success is now strict. A random non-empty current value no longer counts as success. It must match the intended value or known normalized variant.

### 7. Final TP screenshot recovery

A final aggregate pass now rescans each phase folder before the report is written. If TP screenshots exist on disk but were omitted from phase summary, the verifier attaches them and removes the screenshot-only fatal.

## Validation

```text
pytest -q
200 passed in 7.58s
```

## Expected next run

Expected statuses:

```text
Data Map: pass
Source Document Type: pass
Target Document Type: pass
Rule: pass or pass_with_warnings
Source Transport Profile: pass
Target Transport Profile: pass
BizFlow: pass or pass_with_warnings only if portal option inventory is empty
```
