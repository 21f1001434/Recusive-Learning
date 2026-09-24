# v2.1.8 — PyAutoGUI MCP Governed Desktop Fallback

## Decision

PyAutoGUI MCP is useful as a **tertiary desktop/native fallback**, not as a replacement for Playwright MCP. Normal HIP web controls remain semantic-proof -> Playwright MCP.

## Implemented

1. `hip_id_agent/pyautogui_mcp.py` — stdio MCP adapter for the `pyautogui-mcp` package.
2. Capability contract: `pyautogui_size`, `pyautogui_position`, `pyautogui_click`, `pyautogui_write`, `pyautogui_press`, `pyautogui_hotkey`, `pyautogui_screenshot`.
3. BrowserSession starts/closes the desktop MCP on a visible Windows runtime and attaches it to the existing PyAutoGUI safety tool.
4. Existing locator fallback now prefers PyAutoGUI MCP; direct local PyAutoGUI is compatibility-only.
5. Action provenance records whether the fallback was `pyautogui-mcp` or `local-pyautogui`.
6. Stable locator bounding-box checks and exact post-action verification remain mandatory.
7. Native/browser-chrome coordinate recovery requires explicit evidence provenance and confidence >=0.97.
8. Save/Create/Submit/Delete/Deploy/Publish/Update mutations remain blocked through desktop fallback by default.
9. Live readiness exposes PyAutoGUI MCP independently. It is a warning when optional and a blocker only if explicitly required.
10. The package pins `pyautogui-mcp==2026.1.101837` on Windows while retaining `pyautogui==0.9.54` for compatibility fallback.

## Architecture

```text
Customer input / canonical values
        -> AutoWebGLM planning
        -> HIP Intelligence MCP semantic proof
        -> Playwright MCP (normal web execution)
        -> deterministic Playwright compatibility path where already allowed
        -> PyAutoGUI MCP (tertiary visible/native fallback only)
        -> exact DOM/value/effect verification
        -> Chrome DevTools MCP independent witness
```

PyAutoGUI MCP never becomes the normal element-discovery layer and does not receive arbitrary Python-execution capability from this application.
