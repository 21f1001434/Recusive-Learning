# Final verification — AutoGen AgentChat 0.7.5 assured HIP autonomous agent

## AutoGen integration

The codebase already contained Microsoft AutoGen AgentChat usage, but the prior requirements allowed any version >=0.4.0 and strict autonomous execution could fall back to direct Dell AIA REST. This build makes AutoGen 0.7.5 mandatory for the autonomous mission.

Pinned packages:
- autogen-agentchat==0.7.5
- autogen-core==0.7.5
- autogen-ext[openai]==0.7.5

Autonomous mission preflight verifies the exact installed versions and imports before opening HIP. It writes `autogen_preflight.json` to the run directory. The form planner, section text judge, dependency analyst, and runtime recovery advisor all use `AIAClient.json_decision -> AIAClient.autogen_reply -> AutoGen AssistantAgent -> Dell AIA gpt-oss-120b` in strict autonomous mode.

## Validation

- AutoGen-specific regressions: 6/6 passed.
- Streamlit + mission-assurance focused group: 24/24 passed.
- Pure/offline full suite excluding the single external MCP launcher smoke: 527 passed, 1 deselected.
- External MCP fail-fast smoke: passed separately with an intentionally unavailable `npx` stub, proving fail-fast/no-silent-fallback behavior without requiring network access.
- Total test inventory accounted for: 528 tests.
- Python compileall: passed.

The build container has no package-index network access, so it could not install the public AutoGen wheels for a live import smoke. The runtime preflight in the delivered code performs that verification on the operator machine after `pip install -r requirements.txt`.

## Runtime safety

AutoGen plans, ranks, critiques, and judges. Deterministic Playwright/MCP code remains the only browser executor. Save/Create/Submit/Delete/Deploy remain blocked in capture mode, and API write still requires the existing explicit two-key authorization.
