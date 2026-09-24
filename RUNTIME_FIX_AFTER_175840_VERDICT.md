# Runtime Fix After UHAUL-POASN-FULL-DUMMY-20260709-175840

## Evidence reviewed
Latest uploaded run: `UHAUL-POASN-FULL-DUMMY-20260709-175840`.

The run showed:

- Data Map: pass.
- Source Document Type: pass.
- Target Document Type: failed with zero controls because the Document Type listing did not expose/reopen the `+ Add` form.
- Rule: pass with 22 field steps, meaning the repeatable Conditions row fix is now effective.
- Source/Target Transport Profile: values were filled, but the full E2E verifier still reported zero screenshots even though phase PNG files existed in the KB folders.
- BizFlow: failed with zero controls because the browser was on the HIP Home page / non-BizFlow surface instead of the Manage Biz Flow listing or Create Biz Flow wizard.

## Fixes implemented

### 1. Target Document Type + Add recovery
File: `hip_id_agent/doctype_kb.py`

- Replaced the generic Add finder with a scoped DDS-aware Add finder.
- Excludes nav/header/footer/pagination/cookie/modal/drawer controls.
- Prefers the top toolbar `+ Add` button.
- Added `_force_open_doctypes_listing()` to recover when the SPA leaves the user on HIP Home or a stale listing.
- If initial Add lookup fails, the flow reopens Document Types and retries before marking the phase partial.

### 2. BizFlow navigation recovery
File: `hip_id_agent/bizflow_kb.py`

- Added `_ensure_bizflows_listing_page()`.
- It refuses to treat HIP Home as BizFlow evidence.
- Directly retries `/hybrid-integrations/bizexchange/bizflows`.
- If the route bounces to Home, it expands/clicks BizExchange from the left nav and then clicks Biz Flow / Manage Biz Flow.
- Saves navigation audit files:
  - `bizflow_navigation_audit.json`
  - `bizflow_pre_add_navigation_audit.json`

### 3. Screenshot evidence recovery
File: `hip_id_agent/dummy_fill_e2e.py`

- Full E2E verification now merges screenshot paths from the phase summary `files` map.
- If the summary contains a Windows absolute PNG path, verifier recovers the PNG by filename from the local phase directory.
- This is specifically to stop Source/Target TP from failing golden replication because screenshots exist in KB but were not attached to summary.

## Validation

```text
pytest -q
197 passed in 21.01s
```

## Remaining live dependency

The code cannot be live-executed from this environment because HIP Portal authentication and the user’s Chrome/MCP session are local to the user’s machine. The patch is based on the latest uploaded run artifacts and terminal log.
