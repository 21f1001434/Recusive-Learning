# HIP Portal Agent v2.2.2 Build Certification

Date: 2026-09-08

## Release purpose

v2.2.2 removes client-side output-token caps from Dell AIA text and Gemma vision requests by default, while improving response quality and strict JSON handling.

## Implemented runtime behavior

- Text and vision requests omit `max_tokens` and `max_completion_tokens` by default.
- Optional positive overrides: `AIA_MAX_OUTPUT_TOKENS`, `AIA_TEXT_MAX_OUTPUT_TOKENS`, `AIA_VISION_MAX_OUTPUT_TOKENS`.
- Final assistant content is preferred over reasoning/analysis so gpt-oss reasoning cannot corrupt downstream JSON.
- Reasoning is used only as a fallback when no final assistant output exists.
- Dell/OpenAI response envelopes are normalized across string content, content parts, choice-level output aliases, and Responses-style containers.
- Vision preflight/runtime/section judge/aggregate verification share the robust response extractor.
- Strict multimodal JSON rejects malformed non-JSON output.
- Control Center displays a masked text/vision response preview and output-token policy.
- Existing context/evidence-size budgets remain bounded; only the model output cap is removed.

## Source-tree verification

- Full pytest: **1002 / 1002 PASS**
  - 251 / 251
  - 251 / 251
  - 250 / 250
  - 250 / 250
- Focused uncapped/model compatibility: **72 / 72 PASS**
- New v2.2.2 tests: **13 / 13 PASS**
- Python AST: **234 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.2**
- Wheel build: PASS

The exact packaged ZIP is certified separately after clean extraction and a second complete test run.
