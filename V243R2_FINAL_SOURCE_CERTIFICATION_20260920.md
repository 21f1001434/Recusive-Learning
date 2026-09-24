# V243 R2 Final Source Certification — 2026-09-20

## Release identity

- Product: HIP Portal Agent
- Package version: **2.4.3** (kept for V243 compatibility)
- Release tag: **V243 R2 Production Hardening Final**
- Base: V243 production end-to-end runtime
- Scope: additive production hardening; portal learning/execution behavior remains governed by the existing runtime and live evidence gates.

## Added hardening

1. Execution-input immutability gate re-hashes `input.json`, `config.yaml`, golden references, and the generated task plan immediately before browser dispatch.
2. Governance-ledger integrity gate blocks mutation when the audit ledger contains malformed JSON or a broken hash chain.
3. Production browser lease heartbeat protects long-running missions from stale-lock misclassification.
4. `single_active_browser_session` is now behaviorally honored; the safe default remains `true`.
5. Safe review bundles use a strict structural allowlist and include a per-file SHA-256 manifest; arbitrary Markdown, screenshots, raw DOM/network evidence, and raw `input.json` are not admitted in strict mode.
6. Sanitized failure diagnostics persist exception type and stack frames without local variables.
7. Runtime status exposes the hardening controls and receipts.

## Verification completed on this source tree

- Python `compileall`: **PASS**
- WebUI JavaScript syntax (`node --check`, source + packaged fallback): **PASS**
- Pytest collected: **1,234**
- Pytest passed: **1,233**
- Pytest skipped: **1**
- Pytest failed: **0**
- Full suite was executed in six disjoint test-file batches because the conversation shell imposes a process timeout; every `tests/test_*.py` file was assigned exactly once and every batch returned exit code 0.
- Focused production hardening regression: **PASS**
- Wheel build (offline/no build isolation): **PASS**
- Clean wheel install (`--no-deps` target): **PASS**
- Production class imports from installed wheel: **PASS**
- Fresh wheel without project config: runtime status returns `setup_required` rather than HTTP/runtime failure: **PASS**
- Wheel with `HIP_PROJECT_ROOT`: production lifecycle reports enabled and the new immutability/ledger/safe-bundle controls are active: **PASS**
- Local Chromium final mission UAT: **PASS**
  - lifecycle Edit / Save / Validate / Deploy: PASS
  - final BizFlow status: Deployed
  - final consolidation gate: PASS
  - `mock_only=true`
  - `dell_environment_contacted=false`

## Live-environment boundary

This certification does **not** claim that a Dell tenant was mutated. Dell SSO, actual Dell AIA availability, tenant-specific DOM/component drift, real API contracts, and actual Save/Deploy/Migrate operations remain authoritative live-environment validations. The production mutation gate remains fail-closed and requires the configured role/approval controls plus the explicit mutation authorization contract.
