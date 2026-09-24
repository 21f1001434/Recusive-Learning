# v2.2.2 — Uncapped Dell AIA Output + Text/Vision Response Quality

## Goal

Make Dell AIA `gpt-oss-120b` and `gemma-3-27b-it` return usable, accurately parsed results without imposing a client-side output token cap.

## Runtime behavior

By default the client sends neither `max_tokens` nor `max_completion_tokens`. Dell AIA therefore uses the deployment/gateway native output allowance. An administrator can opt back into a positive client limit with `AIA_MAX_OUTPUT_TOKENS`, `AIA_TEXT_MAX_OUTPUT_TOKENS`, or `AIA_VISION_MAX_OUTPUT_TOKENS`. Values blank, 0, `unlimited`, `uncapped`, `native`, or `auto` mean no cap.

## Text response quality

The response normalizer separates final assistant content from reasoning/analysis. If both are present, only final content is returned to downstream planners/judges. Reasoning becomes a fallback only when the deployment emitted no final answer. This prevents reasoning models from producing combined strings such as `<analysis>...` plus the final JSON object that then fail strict parsing.

Supported final-output envelopes include message content strings/parts, `text`, `output_text`, `generated_text`, `answer`, `completion`, `response`, `result`, Responses-style `output`/`outputs`, candidate/data containers and compatible Dell aliases.

## Vision response quality

Vision preflight and runtime vision now use the same robust response extractor. The strict multimodal JSON path rejects malformed output instead of treating a raw string as a successful structured result. Section vision judging and optional aggregate screenshot verification no longer assume only `choices[0].message.content`.

## UI

The Control Center model cards now show:
- provider/model/latency;
- capability result;
- output-token policy (`native/uncapped` or configured);
- masked response preview;
- any contract warning/error.

## Safety boundary

This removes the model **output token cap**. Existing evidence/prompt/context character budgets remain in place to prevent oversized screenshots/DOM/customer payloads from being sent unnecessarily. Mutation authorization and all fail-closed semantic gates are unchanged.
