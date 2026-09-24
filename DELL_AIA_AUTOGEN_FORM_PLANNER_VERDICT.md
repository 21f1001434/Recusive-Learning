# Dell AIA AutoGen Form Planner Integration Verdict

## Request
Use the uploaded LLM/AutoGen reference code with the Dell `gpt-oss-120b` model so the HIP portal agent can fill forms correctly, learn portal behavior, and keep using MCP tools for browser control.

## Implemented

### 1. Dell AIA / AutoGen adapter hardened
Updated `hip_id_agent/aia_client.py` so it supports the uploaded reference-code environment style:

- `AIA_BASE_URL`
- `DELL_AIA_BASE_URL`
- `AIA_ENDPOINT`
- `HIP_LLM_ENDPOINT`
- `AIA_TOKEN`
- `AIA_STATIC_BEARER_TOKEN`
- `DELL_AIA_TOKEN`
- `AIA_TEXT_MODEL`
- `HIP_LLM_MODEL`
- `AIA_MODEL`
- `DELL_AIA_CLIENT_ID` / `DELL_AIA_CLIENT_SECRET`
- `AIA_CLIENT_ID` / `AIA_CLIENT_SECRET`

Default text model remains:

```text
gpt-oss-120b
```

AutoGen is used first through `autogen_agentchat.AssistantAgent` and `autogen_ext.models.openai.OpenAIChatCompletionClient`. If the installed AutoGen API differs, it falls back to direct REST against Dell AIA only.

### 2. New LLM form-state planner
Added:

```text
hip_id_agent/llm_form_planner.py
```

The LLM planner does not click or type directly. It produces a safe JSON plan for the deterministic MCP/Playwright executor:

- field-to-input key mapping
- dependency edges
- parent values that reveal child fields
- selectors to reject, such as dropdown chevrons and shell buttons
- rescan-after-fill guidance

### 3. BizFlow LLM planning integrated
Updated:

```text
hip_id_agent/bizflow_kb.py
```

Before each BizFlow tab fill, the agent now optionally sends the visible controls, dummy/input values, and state snapshot to Dell AIA / AutoGen. The returned plan is attached to evidence and used conservatively to rank/annotate controls.

The deterministic executor still does the actual fill using MCP/Playwright-safe actions.

### 4. Transport Profile LLM planning integrated
Updated:

```text
hip_id_agent/transport_profile_kb.py
```

Before Transport Profile dependency-order filling, the agent optionally asks Dell AIA / AutoGen to reason over which controls are visible and which values should reveal later fields.

### 5. Config and dependencies updated
Updated:

```text
config.yaml
config.example.yaml
requirements.txt
```

AIA is enabled in the packaged config, but it is safe: if endpoint/token are not configured, the deterministic filler continues and records that LLM planning was unavailable.

## Safety model

The LLM is not allowed to save/create/submit/delete/deploy. It is only a planner/judge. Browser execution remains controlled by the existing safe MCP/Playwright layer and the existing never-click policy.

## Validation

```text
pytest -q
202 passed in 7.36s
```

## Run configuration

Set one Dell AIA endpoint style:

```powershell
$env:AIA_BASE_URL="https://aia.gateway.dell.com/genai/dev/v1"
```

or:

```powershell
$env:AIA_ENDPOINT="https://aia.gateway.dell.com/genai/dev/v1/chat/completions"
```

Then set one token method:

```powershell
$env:AIA_TOKEN="<bearer token>"
```

or:

```powershell
$env:AIA_STATIC_BEARER_TOKEN="<bearer token>"
```

or client credentials if your Dell environment supports `aia_auth`:

```powershell
$env:DELL_AIA_CLIENT_ID="..."
$env:DELL_AIA_CLIENT_SECRET="..."
```

Set the model explicitly:

```powershell
$env:AIA_TEXT_MODEL="gpt-oss-120b"
$env:HIP_USE_LLM_FORM_PLANNER="true"
$env:AIA_USE_AUTOGEN="true"
```

Then run the same full dummy fill command with `--require-mcp`.
