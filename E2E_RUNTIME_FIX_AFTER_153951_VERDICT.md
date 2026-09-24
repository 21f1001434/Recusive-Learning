# E2E Runtime Fix After UHAUL-POASN-FULL-DUMMY-20260709-153951

## Result
Implemented a deeper runtime patch for the remaining failures in the 153951 evidence.

Latest uploaded run evidence showed:

- Data Map: pass
- Source Document Type: pass
- Target Document Type: pass
- Rule: pass
- Source Transport Profile: failed by strict gate/screenshot verification and missed lower SFTP HAFT dependencies in prior capture
- Target Transport Profile: failed by strict gate/screenshot verification and missed lower SFTP HAFT dependencies in prior capture
- BizFlow: failed because the nested Configure Routing + Add drawer did not fill all required dropdown/action fields and one unsafe template-picker app-container click was recorded

## Implemented code changes

### Shared DDS driver
- `select_dds_combobox` now verifies the selected value actually sticks in the UI.
- It normalizes version suffixes such as `(1.0)` so portal options that omit the suffix can still be selected.
- It no longer returns success after typing + Enter when the UI still displays `Select`.
- Dropdown closing is now blur/change only, with no page-shell click.

### Transport Profile
- Form-root detection now prefers the full Create Transport Profile wizard instead of shallow fieldsets.
- Control extraction now includes radio/checkbox controls and nearby-label inference.
- Existing Account is handled as a real DDS radio group, not as text/combobox.
- Use Existing Folder and Splitter Required are handled as real radio/checkbox controls when present.
- The fill loop scrolls/re-expands lower SFTP HAFT sections before resolving Account/Folder/File Pattern/Post Transfer/Document Type controls.
- Post-fill controls are re-captured and re-annotated so the verifier uses the expanded form evidence.
- Relaxed screenshot detection in the verifier now picks up `transport_profile_add_form_after_dummy_fill_no_save.png` when the phase summary omits it.

### BizFlow
- Template launch is now strict: only a visible button/link/role=button inside the B2B-Flow-PubSub-Template card can be clicked.
- Removed the dangerous fallback that clicked the card/root/app container.
- Configure Routing + Add mappings were expanded:
  - Execute Action(s) When -> `route_execute_when`
  - Condition Type -> `route_condition_type`
  - Document Type Name (Version) -> `route_document_type`
  - Operator -> `route_condition_operator`
  - Value -> `route_condition_value`
  - Action Name -> `route_action_name`
  - Type -> `route_action_type`
- Defaults are generated for routing action fields so they are no longer skipped.
- Verifier now counts BizFlow embedded `bizflow_tab_form.controls` correctly instead of reporting `form_controls=0`.

## Local validation

```text
pytest -q
194 passed
```

## Live execution note

This package still requires the authenticated HIP Portal + Chrome DevTools MCP session for final live proof. The code has been patched against the exact 153951 run evidence and the offline test suite passes.
