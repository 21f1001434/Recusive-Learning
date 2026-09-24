# Microsoft AutoGen AgentChat 0.7.5 integration

## Verdict

The prior HIP package already used Microsoft AutoGen AgentChat APIs (`AssistantAgent` and `OpenAIChatCompletionClient`) for Dell AIA reasoning, but its requirements accepted any AutoGen release `>=0.4.0` and the AIA adapter could silently fall back to raw REST. The autonomous mission therefore did not guarantee that AutoGen 0.7.5 was actually the reasoning runtime.

This build makes AutoGen 0.7.5 mandatory for autonomous missions.

## Exact pinned packages

- `autogen-agentchat==0.7.5`
- `autogen-core==0.7.5`
- `autogen-ext[openai]==0.7.5`

## Autonomous mission behavior

`--autonomous-mission` now sets:

- `AIA_USE_AUTOGEN=true`
- `HIP_USE_LLM_FORM_PLANNER=true`
- `HIP_REQUIRE_AUTOGEN_075=true`

Before browser/MCP startup it writes `autogen_preflight.json` and requires all three packages to be exactly 0.7.5 and the AgentChat/OpenAI-extension imports to succeed.

## Components using AutoGen

The Dell AIA text reasoning path is:

`HIP evidence -> AIAClient.json_decision -> AIAClient.autogen_reply -> AutoGen AssistantAgent -> Dell AIA gpt-oss-120b`

This path is used by the semantic form planner, section text judge, dependency analyst, and runtime self-heal recovery advisor. In strict autonomous mode, failure to initialize or execute AutoGen is fail-closed and does not silently switch to the direct REST adapter.

The deterministic Playwright/MCP executor remains the only component allowed to perform browser actions. AutoGen plans/ranks/judges; it cannot bypass the no-save safety policy or invent an unobserved selector/API endpoint.

## Non-autonomous compatibility

The direct Dell AIA REST adapter is retained only for older/non-autonomous utility commands so existing diagnostics do not become unusable when AutoGen is intentionally not installed.
