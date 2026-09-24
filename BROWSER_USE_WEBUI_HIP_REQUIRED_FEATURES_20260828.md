# Browser-Use WebUI → HIP Required Feature Integration

Release: 1.6.0  
Date: 2026-08-28

This release intentionally ports the Browser-Use WebUI capabilities that materially improve the governed Dell HIP agent. It does **not** clone the generic WebUI or weaken HIP governance.

## Implemented HIP-equivalent capabilities

| Browser-Use/WebUI capability | HIP implementation | Default |
|---|---|---|
| Use own browser | `browser_use.use_own_browser=true` + `browser_use.cdp_url`; deterministic Playwright, Browser Use state bridge, Playwright MCP and Chrome DevTools MCP share the same existing Chrome/CDP context | Off |
| Persistent authenticated browser | Managed persistent Chrome user-data-dir across all phases; optional external own-browser CDP mode leaves browser open | On |
| Browser-Use state understanding | `BrowserUseStateBridge`, attached to the same HIP Chrome; state-only/recovery intelligence | On |
| Stop | Mission dashboard and platform UI | On |
| Pause / Resume | Suspend/resume only the HIP Python controller; Chrome remains interactive for SSO/manual recovery | On |
| Human-in-the-loop | Pause → operator interacts with same Chrome → Resume; Dell SSO remains manual when needed | On |
| Vision | Existing Dell AIA vision/section judge and screenshots | On by mission policy |
| Planner / task input | Certified future-task planner, governed change planner, AgentQ/runtime self-heal | On |
| MCP configuration | Pinned Bun-managed Playwright MCP + Chrome DevTools MCP + local HIP Intelligence MCP | On |
| Browser recording | Optional Playwright video recording (`record_video`) | Off |
| Playwright trace | Optional trace zip with screenshots/snapshots/sources controls | Off |
| Download persistence | Download listener saves sanitized files and manifest | On |
| Agent/session history | Bounded masked Browser-Use-style session history plus existing action/network/DOM/portal-brain evidence | On |
| Browser dimensions | HIP managed Chrome viewport remains deterministic at 1440×950; recording size is configurable | On |
| Keep browser open | Supported for externally attached own-browser CDP mode | On |
| Task history | Runs, action sequence, browser session history, capability graph, portal brain and certification evidence | On |
| Clear/reset | Existing run separation and runtime state files; no destructive browser-profile reset is performed automatically | Safe-by-design |
| Dynamic forms / `+` rows | Input-driven repeatable-row engine: prove current count, click section-local `+` exactly required times, verify N→N+1 after every click, then fill row-by-row | On |

## Deliberately not copied

The following generic WebUI options are not required for this Dell HIP solution and are intentionally excluded:

- Generic multi-provider LLM switching. Dell AIA + configured governed models remain the approved model boundary.
- `disable_security` / browser-security weakening. HIP automation must not lower browser security controls.
- Generic remote WSS/cloud-browser provider routing. Dell SSO should remain in the approved local/corporate browser unless a separately approved remote-browser architecture is introduced.
- Free-form Browser-Use Agent mutation authority. Browser Use is perception/recovery only; state-changing actions stay behind HIP preview/approval/confirmation/audit gates.
- Gradio UI cloning. The project retains the HIP-specific Streamlit mission dashboard and broader Streamlit platform UI.
- GIF-only history generation. HIP already stores stronger evidence: screenshots, optional video, optional Playwright trace, action/DOM/network evidence and audit artifacts.

## Bun commands

```powershell
bun install
bun run ui
```

Full platform UI (backend must also be running):

```powershell
bun run api
bun run platform
```

Pinned MCP tools:

```powershell
bun run mcp:playwright
bun run mcp:chrome
```

## Own-browser CDP mode

Use this only when you intentionally start Chrome with a remote-debugging endpoint and want the agent to attach to that already-authenticated browser.

```yaml
browser_use:
  enabled: true
  attach_same_browser: true
  use_own_browser: true
  cdp_url: "http://127.0.0.1:9237"
  keep_browser_open: true
```

If own-browser attachment fails and `fail_open_if_unavailable: true`, the agent falls back to the normal governed managed persistent Chrome. It never falls back to an ungoverned free-form Browser-Use agent.

## Optional evidence

```yaml
browser_use:
  record_video: true
  capture_playwright_trace: true
  save_downloads: true
  save_session_history: true
```

Video and trace are disabled by default because they can be large and may contain portal content.

## Dynamic repeatable-row contract

If input JSON requires two rows and HIP initially exposes one row:

1. Resolve the correct repeatable section.
2. Fill/validate row 1.
3. Count the current rows and prove the count is 1.
4. Resolve the section-local Add/`+` control.
5. Click exactly once.
6. Prove row count changed from 1 to 2.
7. Re-resolve controls after Angular/DDS rerender.
8. Fill row 2 from the second JSON object.
9. Validate final row count and values before continuing.

This applies to Rule Conditions, BizFlow nested sections, multi-attribute Document Types, and compatible repeatable Transport Profile sections.
