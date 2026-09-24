# HIP Portal SSO Redirect and Dual-MCP Same-Surface Fix

## Triggering live failure

The authenticated dry run reached browser startup and navigated to the Data Maps URL. Dell redirected the page to `myaccess.dell.com` for SSO. Python Playwright reported `net::ERR_ABORTED`, which is normal when the requested document is replaced by an identity-provider redirect.

The old navigation code immediately executed the final dual-MCP same-surface gate against the temporary SSO URL. Chrome DevTools MCP had not exposed that redirect page yet, returned no parsed pages, and the run failed before the user could complete SSO.

## Root causes

1. Temporary SSO redirects were treated as failed HIP navigation.
2. The dual-MCP gate ran before authentication returned to the requested HIP module.
3. Chrome DevTools MCP used the legacy `--browserUrl` spelling instead of the current official `--browser-url` flag.
4. `list_pages` parsing understood only one text format and could return an empty page list for newer/structured output.
5. The dual-MCP check performed only one immediate attempt instead of allowing the DevTools target list to settle.
6. Playwright MCP attachment was inferred from a snapshot rather than verifying its actual current URL.

## Runtime correction

### SSO-aware navigation state machine

Navigation now classifies the browser state as:

- `target`: requested HIP module is rendered and usable;
- `sso_redirect`: Dell/corporate authentication page is active;
- invalid: neither target nor an approved SSO transition.

An SSO redirect is accepted only as a temporary state. It is recorded in `mcp_runtime/sso_navigation_transition.json`, with URL query strings removed so state tokens are not stored.

### Post-authentication gate

After the user completes SSO, the session:

1. examines all pages in the persistent browser context;
2. adopts the exact requested HIP module tab, including SSO popup/tab transitions;
3. navigates to the requested module if the identity provider returned to a generic portal home page;
4. polls Chrome DevTools MCP until the page becomes visible;
5. checks the exact host/path seen by Python Playwright, Playwright MCP and Chrome DevTools MCP;
6. writes `mcp_runtime/sso_completion_gate.json` and `mcp_runtime/dual_mcp_same_surface.json`;
7. remains fail-closed if the three observers do not agree after the configured timeout.

### Chrome DevTools MCP compatibility

- Same-browser attachment now uses `--browser-url=http://127.0.0.1:<port>`.
- `--experimentalPageIdRouting` is retained.
- Page parsing supports markdown, numbered-list, JSON text, and structured response formats.
- Matching ignores volatile query strings but requires the same host and path.

### New configuration

```yaml
mcp:
  require_dual_mcp_same_surface: true
  dual_mcp_same_surface_timeout_seconds: 30
  dual_mcp_same_surface_poll_seconds: 1
```

## Safety

The patch does not relax the mutation policy. Save, final Create, Submit, Delete, Deploy, Publish, Update and related actions remain blocked. The failed run stopped during navigation and did not fill or mutate a HIP form.

## Local verification

- Python compilation: PASS
- Full tests: 250 passed
- SSO `ERR_ABORTED` redirect regression: PASS
- Same-surface deferral during SSO: PASS
- Post-redirect DevTools polling: PASS
- Chrome DevTools page formats: PASS
- Official `--browser-url` argument: PASS
- Query-token redaction: PASS

## Live status

The implementation is ready for another authenticated no-save rerun. A live HIP pass is not claimed until the rerun completes all phases and section judges.
