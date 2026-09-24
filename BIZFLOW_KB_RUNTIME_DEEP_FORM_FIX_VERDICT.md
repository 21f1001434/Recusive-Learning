# BizFlow KB Runtime/Deployment Deep Profile + Full Dropdown Capture Fix Verdict

## Verdict
Implemented one more BizFlow KB patch because the previous final HTML captured definition-level BizFlow data and Add-form structure, but did not fully capture the expanded runtime/deployment information visible on the BizFlow View screen.

## What was missing before
The previous run captured:
- 249/249 BizFlow inventory rows
- 249/249 numeric IDs
- 249/249 definition/list deep profiles
- Add BizFlow multi-tab form controls
- required fields
- dropdown controls
- dummy-fill evidence

But the screenshot showed additional View-screen/runtime details that were not part of the generated final HTML:
- Deployment Status
- Deployment Date
- Deployed By
- Vulnerability
- Latest Version
- Source Details expanded section
- Target Details expanded section
- Flow Servers tab
- Interface Details tab
- TP-ROUTING details
- FLOW-ROUTING details
- Service Name
- Service Health
- Deployment Group Name
- Service Manager Name
- Service Manager Health
- Kubernetes Namespace Name
- Data Center
- Deployment Log / Transaction Log / Application Log links
- Image Name
- No Of Instance
- Uptime

## What is fixed now
Added runtime/deployment profile capture after normal deep-profile enrichment:

1. Opens each BizFlow in View mode.
2. Searches by Flow Name to avoid opening the wrong row.
3. Clicks only safe View/expand/tab controls.
4. Captures visible runtime/deployment DOM text.
5. Captures visible environment tabs such as DEV/TEST/PROD where available.
6. Expands Basic Details, Source Details and Target Details.
7. Clicks Flow Servers and Interface Details tabs.
8. Extracts TP-ROUTING and FLOW-ROUTING service blocks.
9. Extracts runtime labels and log links.
10. Writes runtime output files into the upload ZIP.

## Form/dropdown fix
The Add BizFlow form capture was also improved:

- Keeps the already working 4-tab flow:
  - Flow Details / Basic Details
  - Configure Source / Source Details
  - Configure Target(s) / Target Details
  - Configure Routing
- Keeps Configure Routing nested + Add capture.
- Adds best-effort dropdown option harvesting.
- Opens dropdowns only to read options, does not select real production values.
- Keeps dummy-fill no-save behavior.
- Still blocks Save/Create/Submit/Delete/Deploy.

## New output files
The run summary ZIP now includes:

- `bizflow_runtime_deployment_profiles.json`
- `bizflow_runtime_deployment_report.json`
- existing `old_bizflows_deep_profiles.json`
- existing `bizflow_tab_form_kb.json`
- existing `bizflow_dropdowns.json`
- existing `bizflow_required_fields.json`
- existing `bizflow_dummy_fill_plan.json`

## New expected counts
After running the full command again, expect:

- `old_bizflows`: 249
- `old_bizflows_with_numeric_id`: 249
- `deep_profiles_captured`: 249
- `runtime_profiles_captured`: should improve toward 249 if the View screen opens for all rows
- `form_controls`: should remain > 0
- `dropdowns`: should remain > 0 and should now include option counts where options are readable
- `required_fields`: should remain > 0
- `dummy_fill_attempts`: should remain > 0

## Safety
The patch only performs safe read-only clicks:

- View
- environment tabs
- Expand All
- Basic/Source/Target Details
- Flow Servers
- Interface Details
- Configure Routing + Add for form discovery only

It still blocks:

- Save
- Create
- Submit
- Delete
- Remove
- Deploy
- Enable/Disable

## Acceptance
`pytest -q` result:

```text
183 passed
```
