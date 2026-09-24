# Dell AIA Existing `.env` Compatibility Verdict

Implemented.

The HIP form planner now reuses the existing Dell AIA `.env` pattern without requiring the user to rename variables.

Supported existing variables:

```text
USE_DELL_SSO=true
DELL_AUTH_MODE=auto
MODEL_NAME=gpt-oss-120b
BASE_URL=https://aia.gateway.dell.com/genai/dev/v1
TEMPERATURE=0.2
CLIENT_ID=<masked>
CLIENT_SECRET=<masked>
```

Mapping:

| Existing `.env` key | Used as |
|---|---|
| `BASE_URL` | Dell AIA base URL; converted to `/chat/completions` automatically |
| `MODEL_NAME` | Text model name, e.g. `gpt-oss-120b` |
| `TEMPERATURE` | Chat temperature |
| `DELL_AUTH_MODE` | Auth mode: `auto`, `sso`, `client_credentials`, `env`, `command` |
| `CLIENT_ID` / `CLIENT_SECRET` | Client credentials via `aia_auth` package or configured token flow |
| `USE_DELL_SSO` | Enables Dell SSO-compatible planning configuration |

Behavior:

- `HIP_USE_LLM_FORM_PLANNER=true` is no longer mandatory when the existing Dell AIA env is present.
- If `BASE_URL`, `MODEL_NAME`, `CLIENT_ID`/`CLIENT_SECRET`, `DELL_AUTH_MODE`, or `USE_DELL_SSO` exists, the form planner is enabled automatically.
- The deterministic MCP/Playwright filler remains the executor.
- Dell AIA / AutoGen remains the planner/judge only.
- No public OpenAI fallback is added.

Validation:

```text
pytest -q
204 passed, 4 warnings
```
