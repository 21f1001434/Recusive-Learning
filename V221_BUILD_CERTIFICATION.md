# HIP Portal Agent v2.2.1 Build Certification

Date: 2026-09-08

## Scope

v2.2.1 fixes the remaining Dell AIA text-model availability failure observed in the live Windows run while preserving the v2.2.0 Gemma vision/MCP fixes.

## Implemented

- Dell AIA assistant-text extraction now recognizes choice-level and message-level `reasoning_content`, `reasoning`, `analysis`, `answer`, `completion`, `generated_text`, `output_text`, `output`, `response`, and related text-bearing aliases.
- The text availability probe no longer uses a 32-token completion cap. `AIA_TEXT_PROBE_MAX_TOKENS` defaults to 256 and is clamped to a minimum of 128.
- Empty HTTP-success completions now report response keys, choice keys, message keys, and `finish_reason` for actionable diagnostics.
- Vision, SSO, Playwright MCP, DevTools MCP, HIP Intelligence MCP, and PyAutoGUI MCP behavior remains unchanged.

## Source verification

- Full pytest: **989 / 989 PASS** (248 + 247 + 247 + 247)
- Focused model/readiness compatibility: **131 / 131 PASS**
- New v2.2.1 Dell AIA regression suite: **10 / 10 PASS**
- Python AST: **233 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.1**

The post-package exact-ZIP verification is recorded separately in the final release certificate.
