# V230 — Autonomous, Adaptive and Intelligent Data Map Execution

## Objective

Data Map is no longer treated as a recorded click sequence. The business goal comes from the current input JSON/state graph. The live Dell HIP page is re-observed on every adaptive cycle and before every state transaction. Prior screenshots, learned KB facts, and known DDS semantics are ranking priors only.

## Live control loop

1. Navigate to the Data Maps listing using the persistent authenticated browser.
2. Observe the current route, DOM/accessibility surface, MCP snapshots and AutoWebGLM simplified HTML.
3. Find the page-level structural Add control semantically. DDS kind/size and top-right position are positive ranking signals, never mandatory selectors or coordinates.
4. Execute Add through the BrowserSession hybrid broker: AutoWebGLM intent gate, semantic proof, PyAutoGUI MCP first when a trustworthy physical target exists, Playwright MCP fallback, then governed Python Playwright fallback.
5. Prove that an in-page map create/edit drawer is open. The detector accepts current semantic variants rather than requiring only the literal title `Create Map`.
6. Compile the current input JSON into canonical business-goal nodes. Missing input values are not fabricated.
7. Capture the live controls from the active drawer. Cached selectors are evidence only and are never reused as durable locators.
8. Bind business nodes to current controls with deterministic semantic scoring. If a required binding is ambiguous, Dell AIA/AutoGen may advise a mapping, but it may only select a control that exists in the current DOM and a key that exists in the current mission graph.
9. Execute non-file controls through the common DDS/BrowserSession broker. Every action re-captures the current Angular generation, verifies the target, executes, then reads the committed value back.
10. Re-observe the form after parent selections because DDS may mount, replace or reveal child controls.
11. Discover and execute file upload controls only after dependencies have settled. The upload contract validates accepted file type and portal acceptance.
12. Execute the complete graph again as the authoritative goal check. This repairs values lost during rerender and requires exact + authoritative execution evidence.
13. Discover any live required control not represented by the mission. If it is not already populated by the portal and cannot be mapped to a real input value, return `NEEDS_INPUT`; never synthesize a value.
14. Learn verified semantic relationships and dependency transitions. Never persist CSS selectors, generated Angular IDs, DOM indexes, or screen coordinates as long-term knowledge.
15. Continue bounded adaptive cycles while there is progress. Repeated no-progress and wall-clock guards remain fail-closed.
16. Only after the exact target state is proven may the section judge allow phase handoff. Final Save/Create/Submit/Deploy mutation remains governed separately.

## AutoWebGLM role

AutoWebGLM is active at two levels. V230 records a live simplified-HTML observation for each autonomous cycle, and BrowserSession calls the AutoWebGLM primary decision gate before governed click/fill/key interactions. AutoWebGLM is not allowed to invent an unvetted final mutation. The deterministic semantic layer defines the business intent and the physical executor performs the action.

## Executor selection

The physical action path is:

`semantic target -> PyAutoGUI MCP -> Playwright MCP -> Python Playwright -> exact read-back`

PyAutoGUI is preferred when its target rectangle is trustworthy. PyAutoGUI failure does not fail the business task if Playwright can prove and execute the same current-generation control. Playwright MCP receives a canonical live CSS selector derived from the proven Locator, never a human description such as `Data Maps top-right + Add`.

## Adaptation to portal changes

V230 can tolerate field order changes, DDS wrapper changes, current-generation selector changes, title variants such as Create/Add/New Map, delayed child mounting, rerenders after dropdown selection, and newly introduced required controls. A new label can be accepted through Dell AIA semantic advice only if the advised key exists in input.json and the advised control is uniquely present in the live DOM.

## Anti-fabrication behavior

The canonical state graph contains only current input values. A required live control with no mapped input and no portal-provided value is surfaced in `needs_input`. Screenshot examples and `DEFAULT_DUMMY_DATA_MAP` are not used to invent missing values in the strict mission graph.

## Evidence

The Data Map phase writes `datamap_autonomous_form_execution.json` and `autonomous_form_runtime/autonomous_form_runtime.json`. Each adaptive cycle includes the active-surface gate, current control-shape fingerprint, live semantic binding diagnostics, bounded AutoWebGLM observation, advisory mappings, non-file execution, file execution, complete goal verification, and uncovered required controls.

## Safety boundary

The autonomous form runtime does not lower mutation protections. Save/Create/Submit/Delete/Deploy remain subject to the existing governance and mutation-dispatch guards. If a physical mutation may have been dispatched but its outcome is unknown, the alternate executor does not blindly replay it.
