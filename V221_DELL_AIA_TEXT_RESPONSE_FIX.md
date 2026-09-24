# v2.2.1 Dell AIA text-response reliability fix

Observed live state: Gemma vision, browser/CDP, SSO, Playwright MCP, DevTools MCP, HIP Intelligence MCP and PyAutoGUI MCP passed, while the text probe received HTTP success with a `choices` response but no extractable assistant text.

Implemented:
- configurable text probe completion budget `AIA_TEXT_PROBE_MAX_TOKENS` default 256, minimum 128;
- broader Dell/OpenAI-compatible assistant text extraction across choice/message aliases;
- choice/message/finish diagnostics for empty HTTP-success completions;
- existing task-specific judges remain fail-closed.
