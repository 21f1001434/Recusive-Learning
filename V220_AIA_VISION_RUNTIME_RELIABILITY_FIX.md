# HIP Portal v2.2.0 — Dell AIA + Vision + MCP Runtime Reliability Fix

This release closes the live Windows issues observed during v2.1.9 runtime certification.

## Fixed

1. **Deterministic `.env` loading**
   - Backend and CLI load the project-root `.env` explicitly.
   - `HIP_ENV_FILE` can point to an external `.env` when VS Code/Bun is launched from another directory.
   - Existing process environment still wins by default.

2. **Dell AIA text false-negative probe**
   - HTTP-success/non-empty model output now proves availability.
   - Failure to echo the synthetic marker exactly is reported as a warning, not as model unavailability.
   - Task-specific HIP judges remain fail-closed, so this does not weaken action safety.

3. **Dell AIA response-envelope compatibility**
   - Supports string content, content-part arrays, `reasoning_content`, `choices[].text`, `output_text`, and Responses-style `output` arrays.

4. **Vision model resolution**
   - `VISION_MODEL_NAME=gemma-3-27b-it` is supported directly.
   - Existing aliases remain supported: `HIP_VISION_MODEL`, `AIA_VISION_MODEL`, `VISION_MODEL`, `GEMMA_MODEL_NAME`, `GEMMA_MODEL`.
   - Internally discovered models no longer overwrite the operator's canonical environment variable.

5. **Real multimodal capability probe**
   - Replaced the fragile 2-pixel probe with a deterministic 64×32 red/blue image.
   - Supports both `max_tokens` and `max_completion_tokens` Dell/OpenAI-compatible request shapes.
   - Uses the same robust response extraction as the text client.

6. **PyAutoGUI MCP screenshot transport**
   - MCP stdio stream limit increased from asyncio's ~64 KiB default to a bounded 32 MiB default.
   - The live certificate requests only a 128×128 read-only screenshot region when the MCP tool supports regions.
   - A PyAutoGUI screenshot failure is isolated to the PyAutoGUI check instead of collapsing the entire live session into a generic exception.

7. **Safer local configuration**
   - `.gitignore` now excludes `.env`, runtime receipts, caches, build output, and local browser evidence.

## Recommended `.env`

```dotenv
BASE_URL=https://aia.gateway.dell.com/genai/dev/v1
MODEL_NAME=gpt-oss-120b
VISION_MODEL_NAME=gemma-3-27b-it
TEMPERATURE=0.2
DELL_AUTH_MODE=auto
USE_DELL_SSO=true
CLIENT_ID=<your-local-client-id>
CLIENT_SECRET=<your-local-client-secret>
HIP_USE_LLM_FORM_PLANNER=true
AIA_USE_AUTOGEN=true
HIP_REQUIRE_TEXT_JUDGE=true
HIP_REQUIRE_VISION_JUDGE=true
HIP_SKIP_VISION_CAPABILITY_PROBE=false
```

Keep real credentials only in the local `.env`; never commit or package them.
