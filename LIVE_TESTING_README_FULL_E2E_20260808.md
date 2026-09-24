# Live Testing — Full E2E AgentQ Streamlit + UI/API Mission

## Recommended first run

From the extracted project directory in PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
.\RUN_STREAMLIT_UI.ps1 -Port 8501
```

Open `http://localhost:8501`.

Use **API mode = capture** for the first live validation. This mode fills and verifies the forms, captures API request/response evidence, and blocks mutating Create/Save/Submit traffic from reaching Dell.

## Before Start Mission becomes available

The Preflight tab must show:

- Data Map: 100% input coverage
- Source Document Type: 100%
- Target Document Type: 100%
- Rule: 100%
- Source Transport Profile: 100%
- Target Transport Profile: 100%
- BizFlow: 100%
- Golden screenshots: Ready
- Upload assets: Ready
- No blocking cross-object input issues

The included input contains 179 scalar phase leaves, all of which are now accounted for.

## Autonomous sequence

The mission uses one authenticated Chrome session and progresses:

`Data Map -> Source Document Type -> Target Document Type -> Rule -> Source Transport Profile -> Target Transport Profile -> BizFlow`

Each form is dependency-aware. A child control cannot be filled until its parent is committed and the Angular/DDS rerender is observed. Repeatable rows are completed and verified one physical row at a time.

## Evidence to inspect after each phase

Review the run directory for:

- phase exact-state lock and verification,
- deterministic trajectory,
- parent/child execution contract,
- maximum-observability evidence,
- form-open API transactions,
- UI-fill API transactions,
- API payload/response bundle,
- UI/API input crosswalk,
- text/vision judge output,
- golden screenshot comparison.

If a phase fails, inspect `runtime_self_heal/<phase>/attempt_*`. The recovery loop should repair the earliest unresolved dependency rather than replaying the complete phase blindly.

## API capture semantics

`capture` mode records the exact mutating request produced by the UI but aborts it before backend mutation, so that blocked submit transaction intentionally has no real backend response. Reference/list/validation/fill API calls do retain their actual response status and redacted body.

## Preserve learned deterministic memory

When replacing an older package, preserve:

`data\hip_memory\portal_brain`

Only judge-approved structural/interaction knowledge is promoted. Values continue to come from the current input JSON.

## Stop condition

Use `Ctrl+C` or the Streamlit Stop button only for an intentional stop or external outage. The until-complete recovery mode is designed to keep exploring/exploiting safe form interactions until the exact phase contract passes.
