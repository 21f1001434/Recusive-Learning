# Deterministic Section Judge Gate — Implementation Verdict

## Problem confirmed from run `UHAUL-POASN-FULL-DUMMY-20260710-033135`

The portal inventory was extensive, but the executor still continued after unresolved section failures. The latest evidence contained unresolved BizFlow attempts for:

- Configure Target: `Action`, `Target Document Type`, `Rule`
- Configure Routing: `Attribute Name/Unit` row 1; `Operator`, `Value`, `Attribute Name/Unit` row 2

The old architecture generated vision prompts after the whole run and did not use the result as a live progression gate. It also read only `AIA_TOKEN` for vision, so the user's existing `CLIENT_ID`/`CLIENT_SECRET` or Dell SSO authentication was ignored.

## Implemented architecture

1. Input JSON is converted to deterministic expected facts for each section.
2. MCP/Playwright fills the section.
3. A deterministic DOM judge checks exact live values, unresolved latest fill attempts, and child-row counts.
4. Dell AIA `gpt-oss-120b` text judge reviews expected input, live DOM state and deterministic findings.
5. Dell AIA multimodal vision judge reviews a fresh section screenshot and optional golden references.
6. If any required judge fails, the section is deterministically repaired and rechecked up to the configured limit.
7. The agent cannot click Continue or move to the next tab/phase until all required judges pass.
8. If approval is still not obtained, execution stops fail-closed and writes a `section_judge_gate.json` / per-attempt judge bundle.

## Authentication compatibility

Both text and vision judges now reuse the existing Dell AIA configuration and token provider:

- `BASE_URL` / `AIA_BASE_URL` / `AIA_ENDPOINT`
- `MODEL_NAME=gpt-oss-120b`
- `CLIENT_ID` + `CLIENT_SECRET`
- `DELL_AUTH_MODE=auto`
- `USE_DELL_SSO=true`
- static bearer token aliases when available

A real vision-capable Dell AIA model must be configured with one of:

- `HIP_VISION_MODEL`
- `AIA_VISION_MODEL`
- `VISION_MODEL_NAME`
- `VISION_MODEL`

`MODEL_NAME` is attempted only as a final fallback; if that deployment is text-only, the strict gate blocks and records the model error instead of silently proceeding.

## New evidence

- `section_judges/<section>_judge_attempt_<n>.json`
- `section_judges/<section>_judge_attempt_<n>.png`
- `<phase>/section_judge_gate.json`
- aggregate `section_judge_results`
- aggregate `blocked_phase`

## Validation

`pytest -q` → `208 passed`
