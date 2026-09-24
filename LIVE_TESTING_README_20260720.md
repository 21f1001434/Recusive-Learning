# HIP Portal AgentQ — Live Testing Baseline

## Fixed in this baseline

1. `--require-mcp` now enforces all three MCP services:
   - Playwright MCP
   - Chrome DevTools MCP
   - local browserless HIP Intelligence MCP
2. The shipped YAML files use portable `./runs` output instead of a user-specific OneDrive path.
3. Windows MCP commands use `npx.cmd` from `PATH` instead of assuming Node.js is installed in one fixed directory.
4. `config.yaml`, `config.example.yaml`, and the strict Windows profile now include the same portal-learning and runtime-self-heal settings.
5. Stale documentation claiming that no local MCP server exists was corrected.
6. A one-command PowerShell live runner was added.
7. Rules now creates every extra Conditions row required by `input.json` using the small `+` next to **Conditions**, matching the existing Actions-row behavior.
8. A Conditions row is filled and exactly verified before the next `+` click; the click must produce exactly one new Angular form-array row.
9. **Execute Action(s) When** now binds through its exact DDS label and Angular control identity instead of tying with Condition Type or Operator.
10. DDS comboboxes in Rules are committed through the DDS control driver rather than native text assignment.

## Preserve learned memory

Before replacing an older package, copy this directory into the new package unchanged:

```text
data\hip_memory\portal_brain
```

Do not copy interrupted run folders into the application source.

## Recommended Windows setup

```powershell
cd C:\hip_agent\HIP_PORTAL_AGENTQ_COURSE_ARCHITECTURE_MCP_FIX
python -m venv venv
.\venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

Fill only your real Dell AIA endpoint/authentication and vision deployment values in `.env`. Do not place credentials in YAML or source files.

## Run the complete live test

```powershell
.\run_live_full_dummy.ps1 `
  -Config .\config.mcp-required.windows.yaml `
  -InputJson .\examples\uhaul_poasn_full_dummy_input.json `
  -Customer UHAUL-POASN-FULL-DUMMY `
  -RunsDir C:\hip_runs `
  -VisionModel <your-real-dell-aia-multimodal-deployment>
```

The script installs requirements, compiles the project, runs the complete test suite, checks Python/Node/npx, preserves the memory directory, and launches the strict live run.

Use `-SkipInstall` after the environment is already installed. Use `-SkipTests` only for a repeated live rerun after the same package has already passed locally.

## Expected startup evidence

The new run must contain:

```text
mcp_preflight\dual_mcp_capabilities.json
hip_intelligence_mcp_startup.json
agentq_course_architecture_manifest.json
browser_session_reuse_contract.json
```

The registry must report:

```json
{
  "mcp_required": true,
  "mcp_required_count": 3,
  "hip_intelligence_mcp_required": true
}
```

HIP Intelligence startup must report `status: started`. Only Playwright MCP and Chrome DevTools MCP attach to the authenticated browser.

## Safety contract

This command fills and verifies fields but must not click final Save, Create, Submit, Delete, Deploy, Publish, Update, or Confirm actions. It stops on lost surfaces, ambiguous bindings, exact-value mismatches, or failed required judges.

## Evidence to upload after the authenticated run

Upload `UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip` and terminal output. The highest-value evidence files are:

```text
hip_intelligence_mcp_startup.json
mcp_preflight\dual_mcp_capabilities.json
full_dummy_fill_summary.json
runtime_self_heal\runtime_self_heal_summary.json
<phase>\phase_execution_attempts.json
<phase>\phase_exact_state_lock.json
<phase>\agentq_runtime\phase_agentq_summary.json
<phase>\agentq_runtime\action_model_plan_*.json
<phase>\agentq_runtime\action_critic_*.json
```

## Rules Conditions live acceptance evidence

For an input containing `N` condition rows, the Rule phase must produce `repeatable_row_audit.json` with:

```json
{
  "row_count_from_input": "N",
  "final_live_row_count": "N",
  "summary": {
    "clicked": "N minus the initial visible row count",
    "failed": 0,
    "filled_rows": "N",
    "exact_row_count_pass": true,
    "exact_input_pass": true
  }
}
```

Every entry under `clicks` must show `exact_plus_one: true`. The agent must stop without clicking again when the current row is invalid, the Conditions `+` cannot be uniquely located, or the row count does not change by exactly one.
