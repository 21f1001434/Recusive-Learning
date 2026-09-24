# V230 Build Verification

Date: 2026-09-10
Version: `2.3.0`

## Implemented

- Goal-driven autonomous Data Map runtime added in `hip_id_agent/autonomous_form_runtime.py`.
- Data Map no longer uses a hard-coded field execution order.
- Current live DOM/accessibility state is re-observed every adaptive cycle and before each state transaction.
- AutoWebGLM live observation is captured per adaptive cycle and remains the primary interaction decision gate inside BrowserSession.
- Dell AIA/AutoGen binding advice is used only for ambiguous live bindings and is constrained to existing input keys + existing current-DOM controls.
- PyAutoGUI MCP remains preferred; Playwright MCP and Python Playwright remain verified fallbacks.
- Current-generation selectors are allowed for immediate execution but are not durable portal memory.
- Newly discovered required controls with no real mission value are reported as `NEEDS_INPUT` rather than fabricated.
- Data Map create-surface verification accepts semantic variants and is not limited to one exact drawer title.
- Autonomous mission enables progress-driven self-heal while retaining no-progress and wall-clock guards.

## Regression validation

143 test files were executed in four non-overlapping batches:

- Batch 1: 280 passed
- Batch 2: 199 passed
- Batch 3: 260 passed
- Batch 4: 362 passed
- Total: **1101 passed**

The suite includes local Chromium PyAutoGUI-primary interaction, Playwright fallback, all-section same-page form entry, Data Map state reconciliation, upload contracts, AutoWebGLM, AgentQ, MCP, section judge, mission handoff, self-healing and V230 autonomous/adaptive Data Map tests.

## Static/config validation

- 98 Python modules compiled successfully with `py_compile`.
- `config.yaml`, `config.example.yaml`, and `config.mcp-required.windows.yaml` all load successfully.
- Default normal configuration: autonomous Data Map enabled, PyAutoGUI interaction mode `primary`, Playwright MCP fallback enabled, Python Playwright fallback enabled.

## Wheel

Built offline with existing environment tooling using:

`python -m pip wheel . --no-deps --no-build-isolation -w dist`

Artifact: `dist/hip_portal_id_agent-2.3.0-py3-none-any.whl`

The standard `python -m build` command was not available in the validation container because the `build` module is not installed; wheel construction itself completed successfully with pip/setuptools without downloading dependencies.
