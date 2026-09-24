# V228 — PyAutoGUI MCP Primary Interaction + Browser-Use Recovery

## Runtime contract

For HIP web controls, the execution order is now:

1. **AutoWebGLM + HIP semantic evidence** determine what control is intended.
2. **Playwright DOM/accessibility + HIP Intelligence MCP** prove the exact live target when available.
3. **PyAutoGUI MCP is the primary physical actor** for click, fill/search typing, and key presses.
4. **Playwright MCP is the deterministic fallback and effect/value verifier.**
5. **Python Playwright is the final compatibility fallback.**
6. **Chrome DevTools MCP** remains the independent DOM/network/console witness.
7. When the page-level `+ Add` is visibly rendered but cannot be resolved through DOM/accessibility, **Gemma vision + Browser-Use recovery context locate it** and PyAutoGUI MCP may click the high-confidence viewport coordinate. This visual-coordinate path is structural-only and cannot Save/Create/Delete/Deploy.

Final tenant mutations can use PyAutoGUI only after the normal semantic/policy/mutation gates resolve an exact DOM target. If a physical mutation may have been dispatched but its effect cannot be proven, the agent raises `HIP_MUTATION_DISPATCH_OUTCOME_UNKNOWN` and does **not** replay the mutation through a fallback executor.

## Same-page contract

Data Maps, Document Types, Rules, Transport Profiles, and BizFlow all use the same listing-route contract:

`open section -> locate top-right page + Add -> physical PyAutoGUI click -> verify same route -> verify form/intermediate surface`

BizFlow continues:

`+ Add -> same-page template/card picker -> physical click -> multi-tab form`

A route-changing Add/create anchor is rejected for these in-page sections.

## Browser-Use / web-ui

The HIP runtime already uses Browser-Use core as same-browser/CDP **perception and recovery context**. The generic `browser-use/web-ui` project is useful for visual inspection, persistent sessions, own-browser experiments, and reproducing difficult browser behavior in a non-production environment.

It is deliberately **not an execution authority** inside the HIP mission. Running two independent agents that both click the same Dell tab would make action attribution and mutation safety nondeterministic.

Use the optional sidecar in its own Python 3.11 environment:

```powershell
.\RUN_BROWSER_USE_WEBUI_OPTIONAL.ps1 -Setup
```

After the first setup:

```powershell
.\RUN_BROWSER_USE_WEBUI_OPTIONAL.ps1
```

Do not start a free-form WebUI task against the same authenticated Dell HIP browser while the HIP mission controller is running. For the HIP agent itself, keep `browser_use.enabled: true`; its internal `BrowserUseStateBridge` is already attached to the governed browser state when recovery context is needed.

## Local validation

`local_mock_hip/mock_hip.html` is a deterministic same-page HIP mock covering:

- Data Maps
- Document Types
- Rules
- Transport Profiles
- BizFlow template picker + tabs
- field typing
- nested `+ Add Row`

`tests/test_v228_local_mock_pyautogui_primary.py` launches real Chromium and feeds the generated desktop coordinates into an MCP-compatible test adapter. This validates target-to-screen coordinate conversion and the physical-interaction contract without pretending the Linux CI host is a Windows Dell desktop.

A real Windows/Dell certification must still run on the user's visible workstation because PyAutoGUI acts on the current desktop/display and requires a real GUI session.
