# v2.2.6 — PyAutoGUI MCP Active Recovery + Automatic Live Certificate Renewal

Date: 2026-09-09

## Root cause addressed

The live screenshot showed PyAutoGUI MCP itself PASS while the **Real Windows live runtime certification** row was BLOCK. The certificate is time/fingerprint bound, so an expired or mismatched certificate could block Live GO/NO-GO even though Chrome, Dell SSO, Playwright MCP, Chrome DevTools MCP, HIP Intelligence MCP, Dell AIA and PyAutoGUI MCP were healthy.

## Implemented changes

### Automatic real Windows certificate renewal

When Live GO/NO-GO is requested and the current runtime certificate is missing, expired, fingerprint-mismatched or otherwise invalid, the backend automatically runs the same real non-mutating Windows certification when prerequisite static/browser/text/vision/runs-path checks are healthy and no mission is active. After certification it re-verifies the newly written certificate and feeds that proof into the readiness gate.

The auto-renewal still requires the actual target workstation and still proves:
- headed Chrome/browser health,
- Dell SSO authenticated HIP session,
- same-browser Playwright MCP,
- Chrome DevTools MCP witness,
- HIP Intelligence MCP,
- Dell AIA text + vision,
- PyAutoGUI MCP size/position/screenshot capability,
- tenant mutation channels disabled during certification.

### PyAutoGUI MCP as an active governed recovery channel

Normal execution remains Playwright MCP first. If that exact semantically proven action fails:
- safe/structural click: PyAutoGUI MCP is attempted before local in-process Playwright fallback;
- business-field fill: PyAutoGUI MCP is attempted before local `.fill()` and must pass exact committed-value verification;
- key action: PyAutoGUI MCP is attempted before local Playwright `press()`.

The desktop target is not guessed. It is derived from an already-proven Playwright locator, checked for a stable visible bounding box, converted to a desktop point, executed through the audited PyAutoGUI MCP channel, then verified from browser DOM/trusted events or exact field value.

Final Save/Create/Delete/Deploy/Publish/Submit mutation controls remain blocked from PyAutoGUI by default.

### Certificate identity/path correctness

- Runtime fingerprints now include the package version so a certificate from an older build cannot authorize a newer runtime.
- `live_runtime_certification.latest_certificate_relative_path` is now actually honored by both certificate writer and verifier.

## Shipped config

`config.yaml` enables:

```yaml
pyautogui:
  enabled: true
  mcp_enabled: true
  mcp_required: true
  prefer_mcp: true
  prefer_mcp_before_local_web_fallback: true
  use_for_structural_web_recovery: true
  use_for_form_fill_recovery: true
  use_for_key_recovery: true
  allow_mutation_clicks: false

live_runtime_certification:
  enabled: true
  require_for_live_go_no_go: true
  require_pyautogui_mcp: true
  auto_refresh_on_live_readiness: true
  auto_refresh_only_when_mission_idle: true
```

## Safety contract

PyAutoGUI MCP is an execution fallback only after semantic target proof. It does not become a free-form coordinate agent and does not replace Playwright MCP as the normal HIP web executor.
