# E2E Form Fill Runtime Fix Verdict

Implemented fixes for the UHAUL-POASN full dummy fill runtime failures observed in `UHAUL-POASN-FULL-DUMMY-20260709-142944`.

## Implemented

1. Added `hip_id_agent/dds_control_driver.py`
   - Active visible form-root detection by phase.
   - Safe DDS combobox selection scoped to the active drawer/wizard.
   - Safe file upload into active form `input[type=file]`.
   - Safe dropdown close without `Escape`.
   - Active surface gates for Data Map, Transport Profile, and BizFlow.

2. Data Map runtime hardening
   - Uses active Create Map root only.
   - Stops treating listing grid as a valid after-fill form.
   - Stable fill order: Map Identifier, Map Name, Map Class, Contivo Version, Status, Map Data, Input Schema, Output Schema.
   - Uses active-root scoped upload for Map Data/Input Schema/Output Schema.
   - Skips readonly/disabled fields instead of counting them as failed fills.
   - Refuses final screenshot/DOM if Create Map surface is lost.

3. Transport Profile runtime hardening
   - `extract_transport_profile_seed(input_data, phase=...)` now isolates source vs target TP.
   - Source TP no longer inherits target partner/account values.
   - Target TP no longer inherits source application/account values.
   - Dependency-ordered wizard fill for SFTP HAFT fields.
   - Re-finds controls after every DDS/Angular re-render.
   - Requires core SFTP HAFT fields before trusting the final screenshot.

4. BizFlow runtime hardening
   - Template launch now scopes click to the `B2B-Flow-PubSub-Template` card root.
   - Avoids treating a global `Outbound` tag/filter as the wizard launch.
   - Active surface gate rejects template picker as final form.
   - Tab-aware field mapping prevents `Document Type Name` / `Attribute Name` from mapping to `flow_name`.
   - Uses DDS combobox selection instead of raw body-wide JS assignment.
   - Carries UHAUL flow identifiers, process/routing values, source/target profile names, rule, and target action values from input JSON.

5. Escape removal for critical form flows
   - Data Map, Document Type, Rules, Transport Profile, and BizFlow dropdown collection no longer uses `Escape` to close DDS dropdowns.
   - Uses safe blank click instead, so the drawer/wizard is not closed accidentally.

## Validation

```text
pytest -q
194 passed, 1 warning
```

## Important runtime note

This patch was validated offline with unit/acceptance tests in the uploaded repo. Live portal verification still requires running the command from your Windows machine with Chrome/MCP session access and the UHAUL uploads directory.
