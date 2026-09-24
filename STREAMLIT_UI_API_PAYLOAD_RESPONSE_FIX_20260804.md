# HIP AgentQ Streamlit UI and API Payload/Response Capture

## Objective

Provide one operator interface for the autonomous Data Map → BizFlow mission and persist the API payload and response evidence generated while opening and filling every form.

## Implemented

### Streamlit mission control

- Starts the existing AgentQ/MCP autonomous mission as a separate process.
- Stops the process without closing the Streamlit server.
- Accepts an uploaded replacement `input.json` after structural validation.
- Supports capture, validate, dry-run and explicitly gated write modes.
- Displays all seven phase states and API evidence counts.
- Shows live console output from the autonomous mission.
- Displays each request payload and corresponding response side by side.
- Displays the input-path → UI-control → observed API-key crosswalk.
- Builds a redacted evidence ZIP while excluding browser profile and Portal Brain directories.

### API payload and response capture

Every observed form API transaction now records:

- transaction/request ID;
- UI phase and stage;
- HTTP method and endpoint template;
- redacted query keys and URL;
- redacted request headers and payload;
- request payload shape;
- response status and success state;
- redacted response headers and payload;
- response payload shape;
- MIME type;
- body-capture result and truncation state;
- initiator and browser evidence source;
- mutation-capable classification.

### Wider response-body capture

The CDP observer previously prioritized literal JSON MIME types. HIP APIs may return JSON using vendor media types or `text/plain`. The observer now requests response bodies for:

- every XHR response;
- every Fetch response;
- POST, PUT, PATCH and DELETE responses;
- all HTTP error responses;
- explicit JSON MIME types.

### Safety boundary

Capture mode intercepts the final Create/Save/Submit mutation and aborts it before network delivery. The exact request payload is persisted in redacted form. A submit response is intentionally unavailable because the backend never receives the mutation. Reference, option, validation and other UI-fill API responses remain available. Real submit responses require the existing two-key write confirmation.

## New phase artifacts

```text
form_open_api_transactions.json
ui_fill_api_transactions.json
form_api_transactions.json
api_payload_response_index.json
form_api_payload_response_bundle.json
```

## Streamlit command

```powershell
python -m pip install -r .\requirements.txt
.\RUN_STREAMLIT_UI.ps1 -Port 8501
```

Then open `http://localhost:8501`.
