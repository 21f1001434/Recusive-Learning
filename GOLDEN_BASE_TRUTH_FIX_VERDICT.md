# Golden Base Truth Fix Verdict

This build treats the human-approved golden screenshots as the acceptance target for UHAUL-POASN full dummy fill.

## Latest evidence used
Run bundle: UHAUL-POASN-FULL-DUMMY-20260709-162023

Observed:
- Data Map passed.
- Source Document Type passed.
- Target Document Type passed.
- Rule passed.
- Source Transport Profile failed only because phase verification did not attach the existing after-fill screenshot.
- Target Transport Profile failed only because phase verification did not attach the existing after-fill screenshot.
- BizFlow failed because the runner stayed on the Manage Biz Flow listing; it did not click the real + Add launcher and no wizard controls were captured.

## Implemented fixes

### Golden screenshots are now treated as truth
- Transport Profile screenshots are saved after scrolling back to the top of the Create Transport Profile form, matching the golden Source/Target TP images instead of capturing only the lower SFTP HAFT section.
- Phase verification now scans the phase folder for screenshots when the phase summary omits screenshot references.
- The strict gate now fails if BizFlow only captures the listing/template surface instead of the wizard.

### Transport Profile fixes
- Fixed DDS radio handling for Existing Account and Use Existing Folder. It now clicks the visible Yes/No label/container and updates the associated hidden radio input.
- Fixed field mapping for Existing Account Name, Use Existing Folder, and Subscription Folder. Previously Existing Account Name could map incorrectly to Existing Account.
- Required SFTP HAFT fields now match the golden Source/Target Transport Profile images.
- Verification can count already-visible filled controls when the fill-attempt list is unavailable from a runner path.

### BizFlow fixes
- Fixed + Add detection on the Manage Biz Flow grid. The runner now resolves the exact top-right + Add text/link and climbs to the nearest safe clickable ancestor.
- The + Add resolver explicitly excludes nav/footer/pagination/cookie controls.
- The strict gate still refuses to pass BizFlow unless actual Create Biz Flow wizard controls are captured.

## Local validation

```text
pytest -q
194 passed, 2 warnings
```

## Runtime expectation
Run with MCP required. The next run should:
1. Open Manage Biz Flow.
2. Click the real + Add.
3. Select B2B-Flow-PubSub-Template -> Outbound.
4. Fill Flow Details, Configure Source, Configure Target(s), and Configure Routing according to the golden screenshots.
5. Capture screenshots in golden-equivalent posture.
