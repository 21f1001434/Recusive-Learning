# Live Testing: Streamlit UI + API Payload/Response Capture

## 1. Install dependencies

```powershell
python -m pip install -r .\requirements.txt
```

## 2. Start Streamlit

```powershell
.\RUN_STREAMLIT_UI.ps1 -Port 8501
```

Open:

```text
http://localhost:8501
```

## 3. Configure the mission

Use these defaults unless your paths differ:

- Config: `config.yaml`
- Input: `examples\uhaul_poasn_full_dummy_input.json`
- Runs: `C:\hip_runs`
- Golden screenshots: `golden_screenshots\UHAUL-POASN`
- Upload assets: `uploads`
- API mode: `capture`
- Heavy evidence: enabled

Press **Start mission**, complete Dell SSO in the opened Chrome window, and keep that browser open.

## 4. Review API evidence

In **API payloads & responses**:

- select a run;
- filter by phase;
- select a transaction;
- compare the redacted request payload and response body/status.

Expected phase files:

```text
<phase>\form_api_intelligence\attempt_<N>\form_api_transactions.json
<phase>\form_api_intelligence\attempt_<N>\form_api_payload_response_bundle.json
```

## 5. Submit-response behavior

In `capture` mode, the final Create/Save/Submit request is aborted before Dell receives it. The payload is captured, but no submit response exists by design. Dropdown, list, validation and UI-fill API responses are still captured.

Use `write` only when backend mutation is approved and both confirmations are present:

```powershell
$env:HIP_ALLOW_API_MUTATION = "YES"
```

Then select `write` and type `ALLOW API WRITE` in the Streamlit UI.

## 6. Download evidence

Use the **Evidence** tab to build and download a redacted evidence ZIP. Browser profile, cookies and Portal Brain directories are excluded.
