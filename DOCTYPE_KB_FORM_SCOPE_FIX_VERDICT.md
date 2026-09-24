# Document Type KB Form Scope Fix Verdict

## Verdict
The latest run completed successfully, but the generated Add-form KB contained polluted UI evidence because the Dell page keeps global controls mounted while the Add Document Type form is open.

## Root Cause
The control/dropdown collector scanned the whole document instead of the active Document Type Add form scope. As a result, it captured:

- Listing controls: Search, Table search, Filter by column name, Items per page, Page
- Audit/comment modal field: Provide Comment for this Action
- Cookie preference controls: Marketing, Statistical, Uncategorized, vendor search and checkboxes
- Navigation/dropdown noise inside Data Format Type: Home, BizLink, SecureLink, BizExchange, TransTrack, BizMon

## Fix Applied
- Scoped Add-form control detection to the visible Document Type form/container.
- Excluded footer, navigation, table/pagination, audit modal, and OneTrust cookie controls.
- Filtered portal navigation noise from dropdown option capture.
- Added allowlist cleaning for Data Format Type so it only keeps real format options: XML, JSON, EDIFACT, EDIX12, CSV, FLAT.
- Added post-dummy-fill dropdown recapture and merge, because some dependent comboboxes can populate only after earlier required fields have values.
- Left Data Map logic untouched.

## Validation
```text
121 passed
```

## Expected Next Run
The run should still complete all discovered old Document Types and IDs, but the generated KB should no longer include page/search/pagination/cookie/navigation controls as Document Type Add-form fields/dropdowns.
