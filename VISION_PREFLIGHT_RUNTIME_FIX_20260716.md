# Dell AIA Vision Preflight Runtime Fix — 2026-07-16

## Observed failure

The selector-memory migration worked. The next run stopped before browser startup because strict mode required a multimodal judge but no explicit vision model variable was set.

This was safe fail-closed behavior, but the configuration path was too rigid and the CLI message incorrectly implied that a separate endpoint and token were always required.

## Implemented behavior

1. Reuses the existing `BASE_URL`/AIA endpoint and Dell SSO or client credentials.
2. Supports explicit aliases:
   - `HIP_VISION_MODEL`
   - `AIA_VISION_MODEL`
   - `VISION_MODEL_NAME`
   - `VISION_MODEL`
   - `GEMMA_MODEL_NAME`
   - `GEMMA_MODEL`
3. Supports comma-separated candidate variables.
4. When no explicit model is supplied, safely probes the reviewed Unified-KB candidates in order:
   - `gemma-3-27b-it`
   - `pixtral-12b-2409`
5. A candidate is accepted only after reading a two-pixel red/blue image correctly.
6. `MODEL_NAME=gpt-oss-120b` is never used as a vision fallback.
7. Writes detailed attempted-model evidence to `vision_model_preflight.json`.
8. Adds `python -m hip_id_agent.cli vision-preflight` so vision can be tested without opening HIP.

## Recommended verification

```powershell
python -m hip_id_agent.cli vision-preflight --config .\config.yaml
```

To force the reviewed Gemma candidate:

```powershell
python -m hip_id_agent.cli vision-preflight `
  --config .\config.yaml `
  --vision-model "gemma-3-27b-it"
```

After PASS, rerun the original full command unchanged. You may also add:

```powershell
--vision-model "gemma-3-27b-it"
```

Only use the exact deployment name available to your Dell AIA account. The capability probe will reject unavailable or text-only deployments before the portal opens.

## Debug-only bypass

For a temporary non-acceptance debug run:

```powershell
--no-require-vision-judge --no-vision-verify
```

This bypass does not satisfy the strict live acceptance criteria and must not be treated as a validated run.
