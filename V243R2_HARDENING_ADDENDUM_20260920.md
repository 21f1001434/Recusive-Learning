# V243 R2 Production Hardening Addendum — 2026-09-20

This additive hardening pass keeps package version **2.4.3** for compatibility while tightening the production lifecycle around the already implemented V243 portal intelligence runtime.

## Added in R2

- **Execution input immutability gate**: re-hashes `input.json`, `config.yaml`, golden references and the generated plan immediately before browser dispatch. A drift blocks execution with `blocked_execution_input_drift`.
- **Governance-ledger integrity gate**: mutation runs fail closed if the hash-chained change ledger has been tampered with.
- **Active lease heartbeat**: long production missions refresh the browser lease so an active process is not treated as stale.
- **Real single-session switch**: `single_active_browser_session: false` now actually disables acquisition of the production browser lease; the default remains `true`.
- **Strict safe-review allowlist**: arbitrary Markdown files are no longer automatically included in the review ZIP. The bundle gets its own SHA-256 manifest.
- **Sanitized failure diagnostics**: exception type and stack frames are persisted without local variables; secret-like values are masked.
- **Runtime status exposure** for the new controls and receipts.

## Safety posture

No API schema mutation has been introduced. Portal mutation still requires the existing explicit mutation flag, environment gate, exact confirmation phrase, allowed operator role and any configured approval requirement. Live Dell tenant behavior remains authoritative.
