# v2.1.9 — Live Windows Runtime Certification Gate

## Purpose

v2.1.8 had a correctly implemented PyAutoGUI MCP fallback but its final boundary was still external: the package could not itself certify the user's real Windows desktop, Dell SSO session, Edge/Chrome instance, Dell AIA endpoints and MCP processes before a mission.

v2.1.9 turns that boundary into an executable, fail-closed product gate.

## Runtime sequence

```text
Certify Windows runtime
        ↓
headed persistent Edge/Chrome
        ↓
Dell SSO (human completion only when required)
        ↓
Playwright MCP same-browser snapshot + browser_find capability
        ↓
Chrome DevTools MCP same-surface DOM/network/console witness
        ↓
HIP Intelligence MCP semantic surface/route tools
        ↓
Dell AIA text probe + vision image-understanding probe
        ↓
PyAutoGUI MCP READ-ONLY size + position + screenshot
        ↓
mutation authorization confirmed OFF
        ↓
runtime fingerprint + certificate SHA-256 + expiry
        ↓
runs/.hip_runtime/live_runtime_certificate.json
        ↓
Live GO/NO-GO validates certificate
        ↓
mission receipt may be issued
```

## Non-mutating contract

The certification code does **not** call PyAutoGUI `click`, `write`, `press` or `hotkey`. It does not enable browser mutation authorization. No HIP Create, Save, Submit, Update, Delete, Publish, Finish or Deploy action is executed. The only interactive step is the user's Dell SSO flow in the visible browser when the persistent profile is not already authenticated.

## Integrity / binding

The certificate is accepted only when all are true:

- decision is `GO`;
- it is unexpired;
- its runtime fingerprint matches the current browser/MCP/AIA/semantic configuration;
- its canonical certificate SHA-256 verifies;
- the configured Live GO/NO-GO policy requires/accepts it.

Changing the relevant runtime settings invalidates the certificate and requires a new certification.

## Commands

```powershell
python -m hip_id_agent.cli certify-live-runtime --config config.yaml
```

Then run the existing Live GO/NO-GO from the JavaScript UI or API. The shipped `config.yaml` sets `live_runtime_certification.require_for_live_go_no_go: true`.
