# HIP Portal Agent v2.1.0 — Completion-First Mission Hardening

This release closes the failures observed in the Dell Windows screenshots: deep OneDrive path creation (`WinError 206`), Data Maps no-progress reasoning, CDP/WebSocket reconnect stalls, missing model health probes, and missing human-readable phase execution evidence.

## Runtime contract

`AutoWebGLM primary planner -> official Playwright MCP primary safe-action executor -> deterministic Python Playwright DDS fallback -> PyAutoGUI last resort`.

The authenticated browser is selected before SSO (Edge, then installed Chrome, then Playwright Chromium) and is locked for the mission. MCP transport failure reattaches clients to that same browser; it does not switch browsers.

## Layer 7 phase progression

Each phase is represented by a stable mission identifier (P01-DM through P07-BF). A phase attempt runs under an active structural no-progress watchdog. The fingerprint contains only route and control-state structure (for example filled/not-filled, expanded, selected, checked, disabled); raw form values are not stored. Repeated structural cycles are interrupted. If exact phase completion had already been proven, post-completion reporting/exploration is cancelled without replaying the form.

After exact completion and independent judge approval, the controller actively navigates to the next HIP module with Playwright MCP first and requires Python Playwright, Playwright MCP, and Chrome DevTools MCP to agree on the target route. A handoff failure never replays the completed source phase solely to retry navigation; the next phase owns bounded route recovery.

## Verification

Source regression before packaging: 813/813 PASS. Exact final ZIP fresh-extraction verification is recorded in the external v2.1.0 certification artifact.
