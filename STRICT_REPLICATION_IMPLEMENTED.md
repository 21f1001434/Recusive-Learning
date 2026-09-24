# Strict Replication Implemented

This package implements the hard replication fixes requested after the UHAUL-POASN run showed false pass evidence.

## Implemented

1. **Strict replication gate**
   - New `hip_id_agent/replication_guard.py`.
   - Detects if a phase captured a listing/grid/template picker instead of a filled Add form.
   - Fails the phase instead of marking `pass` or `pass_with_warnings`.
   - Writes `strict_replication_gate.json` in each phase folder.

2. **No false pass**
   - Data Map listing page is now fatal.
   - BizFlow template picker page is now fatal.
   - Missing screenshots are fatal.
   - Missing form controls are fatal for form phases.
   - All failed fill attempts are fatal.
   - DUMMY/_KB value leaks are fatal in exact replication mode.

3. **Exact input.json values**
   - `make_phase_input()` now enables `_replicate_exact_input_values`.
   - Data Map no longer prefixes `DUMMY_` when running full replication.
   - Document Type no longer prefixes `DUMMY_` when running full replication.
   - Rule no longer prefixes `DUMMY_` when running full replication.
   - Transport Profile maps UHAUL input fields such as:
     - `profile_name`
     - `profile_usage`
     - `deployment_group`
     - `interface_type`
     - `interface_environment`
     - `existing_account_name`
     - `document_type`
     - `subscription_folder`
   - BizFlow maps nested UHAUL input sections:
     - `flow_details`
     - `configure_source`
     - `flow_identifiers.conditions`
     - `configure_targets`
     - `process_steps`
     - `configure_routing`

4. **Repeatable rows remain input driven**
   - Input arrays still generate repeatable row plans.
   - Two conditions require one row-level `+ Add` before filling the second row.
   - Five document attributes require four row-level `+ Add` clicks.

5. **Upload verification improved**
   - File upload audit now records `uploaded_file_name`.
   - It checks whether the filename is visible in the page/body or file input after upload.
   - Uploads are still no-save/no-create.

6. **Deep OneDrive safe IO retained**
   - Global safe path handling remains active for raw `Path.write_text`, `Path.open`, JSON, CSV, HTML, MMD and report writes.

## Validation

```text
python -m compileall -q hip_id_agent
PASS

pytest -q --disable-warnings
194 passed in 17.44s
```

## Run

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads"
```

## Expected behavior now

If the portal stays on listing page or template picker, the phase will be marked `failed`, and the blueprint will not be trusted as a successful replication.
