# Rerun Guide: Single SSO for the Entire HIP Workflow

Use the complete package as the new baseline and preserve these existing items:

- `.env`
- `config.yaml`
- `uploads/`
- `golden_screenshots/`
- `data/hip_memory/portal_brain/`

Run the same command. No new CLI argument is required.

## Expected console behavior

At the start of Data Map, the browser may display:

```text
SSO/login required. Complete Dell SSO in the opened browser window.
```

Complete SSO once. For later phases, the run should navigate directly to each HIP link without asking for SSO again.

The progress text may still mention the login/navigation stage because every phase verifies that it is on an authenticated HIP surface. That is an authentication check, not another manual login request.

## Verify one-session reuse

After the run:

```powershell
$LATEST_RUN = Get-ChildItem $RUNS_DIR -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content "$($LATEST_RUN.FullName)\browser_session_final_state.json"
```

Expected for all seven phases:

```json
{
  "start_count": 1,
  "borrow_count": 7,
  "sso_prompt_count": 1,
  "authenticated_once": true,
  "single_persistent_context": true
}
```

List per-phase reuse evidence:

```powershell
Get-ChildItem "$($LATEST_RUN.FullName)" -Recurse -Filter browser_session_reuse.json |
  Select-Object FullName
```

Every reached phase must contain the same `session_id`.

## When another SSO prompt is legitimate

A second prompt is allowed only when the corporate login session genuinely expires or the identity provider invalidates the cookies during the run. The evidence file will record the increased `sso_prompt_count` and the navigation transition.
